# Launch runbook — public marketplace + hosted keys (you run every command)

Goal state: anyone downloads the desktop installer, signs up, installs Figure Creator /
Presentation Creator from the Store, and uses them on OUR keys (free tier, usage logged,
no limits). This card is the exact command sequence; each step says what success looks like.

> Phase note: endpoints are **HTTP-only** for now (your call) — treat this as private
> testing. Before any public announcement, add a domain + ACM cert and a :443 listener
> (the alb module is structured so that lands additively).

## 0. Local end-to-end first (no AWS needed)

```powershell
# terminal 1 — accounts service
$env:ACCOUNTS_INTERNAL_KEY = "devinternal"
.\.venv\Scripts\python.exe -m uvicorn app:app --port 4100   # from v2/deploy/accounts

# terminal 2 — model proxy (custom auth ON)
$env:ACCOUNTS_URL = "http://127.0.0.1:4100"; $env:ACCOUNTS_INTERNAL_KEY = "devinternal"
.\.venv\Scripts\python.exe v2\model_proxy\run-local.py

# terminal 3 — local signed registry
.\.venv\Scripts\python.exe v2\deploy\registry\publish.py --key <keypair.json> --serve

# terminal 4 — the desktop app in hosted mode (NO provider keys needed)
cd v2\clients\desktop
npx cross-env AGENTD_FLAVOR=hosted-dev npm run dev
```

Success: sign-in gate on first launch → signup → Store lists both agents → install →
chat completes; `sqlite3 v2/deploy/accounts/data/accounts.db "select * from usage"` shows
exactly ONE row per model call (written by the model proxy, not the daemon). Sign out →
Settings shows "Your own keys" and BYOK still works.

(Already verified in-repo: unit tests `v2/tests/unit/test_model_proxy.py`, accounts hardening
smoke, and a live E1 run — garbage token → 401, session token → real completion + ledger
row, master key → trusted.)

## 1. Publisher keypair (once, keep OUTSIDE the repo)

```powershell
.\.venv\Scripts\python.exe -c "from agent_runtime.cli.main import main; main()" bundle keygen --out C:\keys\agentd-publisher-key.json
```

Success: prints the base64 PUBLIC key → paste it as `publisher_key` in the flavors
(`v2/clients/desktop/flavors/*/distribution.toml`) at step 5. The JSON file itself is the
PRIVATE half — never commit it.

## 2. Terraform (from v2/infra/environments/dev)

For the existing dev stack's one-time `gateway` → `model-proxy` migration, first apply
with the new service scaled to zero. This lets Terraform rename ECR/ECS/Cloud Map/ALB
resources without asking ECS to pull an image that has not been pushed yet. Then push
the independent image and scale the services up:

```powershell
terraform plan -var=model_proxy_desired_count=0
terraform apply -var=model_proxy_desired_count=0
..\..\..\deploy\scripts\push-images.ps1 -Environment dev -Only model-proxy
..\..\..\deploy\scripts\up.ps1 -Environment dev
```

For a fresh environment, use the normal sequence:

```powershell
terraform init
terraform validate
terraform plan    # review before apply
terraform apply
```

Expect in the plan: ~4 new SG rules (ALB :4000/:443 + service :4000), a `model-proxy` target
group + listener, the S3 registry bucket (+ public-read policy), a new
`ACCOUNTS_INTERNAL_KEY` random password in the app secret, and updated task definitions
for model-proxy/accounts. Success: outputs include `accounts_url`, `model_proxy_url`,
`registry_url`, `registry_bucket`.

If the app secret ALREADY existed before this change (its value is `ignore_changes`),
merge the internal key in by hand in step 3 — a fresh apply seeds it automatically.

## 3. Real secrets

```powershell
aws secretsmanager get-secret-value --secret-id agentd/dev/app --query SecretString --output text
# then write back the full JSON with real provider keys (keep the generated
# LITELLM_MASTER_KEY and ACCOUNTS_INTERNAL_KEY values!):
aws secretsmanager put-secret-value --secret-id agentd/dev/app --secret-string '{"LITELLM_MASTER_KEY":"<keep>","ACCOUNTS_INTERNAL_KEY":"<keep>","GEMINI_API_KEY":"<real>","DEEPSEEK_API_KEY":"<real>"}'
```

## 4. Build + push images, roll the services (from v2/)

```powershell
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin <acct>.dkr.ecr.ap-northeast-1.amazonaws.com

docker build -t <ecr>/model-proxy:latest -f model_proxy/Dockerfile model_proxy
docker build -t <ecr>/accounts:latest -f deploy/docker/Dockerfile.accounts .
docker push <ecr>/model-proxy:latest; docker push <ecr>/accounts:latest

aws ecs update-service --cluster agentd-dev --service agentd-dev-model-proxy --force-new-deployment
aws ecs update-service --cluster agentd-dev --service agentd-dev-accounts --force-new-deployment
```

Success:
```powershell
curl http://<alb-dns>:4000/health/liveliness     # "I'm alive!"
curl http://<alb-dns>:4100/health                # {"ok":true,...}
curl -H "Authorization: Bearer junk" http://<alb-dns>:4000/v1/chat/completions -d "{}"   # 401
```

## 5. Publish the registry + wire the flavors

```powershell
.\.venv\Scripts\python.exe v2\deploy\registry\publish.py --key C:\keys\agentd-publisher-key.json --bucket <registry_bucket output>
# then run the aws s3 sync command it prints
curl https://<registry_url output>                # the signed index.json
```

Edit `v2/clients/desktop/flavors/core/distribution.toml` (and studio): uncomment and fill
`[store] registry_url` + `publisher_key` and `[platform] accounts_url` +
`model_proxy_url` from the terraform outputs.

## 6. Ship the installer

```powershell
cd v2\clients\desktop
npm run dist:core          # or tag vX.Y.Z and let .github/workflows/release-build.yml build it
```

Success on a CLEAN machine/profile: install → sign-up gate → signup → Store shows both
agents → install Figure Creator → generate a figure with zero key setup → the accounts
ledger accrues under that user's account.
