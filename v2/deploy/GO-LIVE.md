# Go live: the token login system, end to end

Takes a stack from "old session login" to "web + desktop + AWS all on signed tokens, billing and
budgets live". Written for PowerShell.

**You run every AWS and Terraform command.** Nothing here is run for you.

---

## 0. Pre-flight — DO THIS FIRST, on an existing stack

The accounts service now reads `AGENTD_IDENTITY_KEK` from the app secret (it wraps the token
signing key at rest). Terraform generates it for a **fresh** stack, but the secret carries
`lifecycle { ignore_changes = [secret_string] }`, so on a stack that already exists Terraform will
**not** add the key.

**ECS fails to start a task whose secret names a JSON key that does not exist.** So if you apply
without this, the accounts service will not come up, and the error (`ResourceInitializationError`)
says nothing about identity.

```powershell
$env:AWS_REGION = "ap-northeast-1"
$ENVIRONMENT = "dev"          # dev | staging | production
$SECRET = "agentd/$ENVIRONMENT/app"

# Read the current secret, add the key if it is missing, write it back.
$cur = aws secretsmanager get-secret-value --secret-id $SECRET --query SecretString --output text | ConvertFrom-Json
if (-not $cur.AGENTD_IDENTITY_KEK) {
  $kek = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object { [char]$_ })
  $cur | Add-Member -NotePropertyName AGENTD_IDENTITY_KEK -NotePropertyValue $kek -Force
  $cur | ConvertTo-Json -Compress | Set-Content -Encoding utf8 kek.json
  aws secretsmanager put-secret-value --secret-id $SECRET --secret-string file://kek.json
  Remove-Item kek.json
  "added AGENTD_IDENTITY_KEK"
} else { "already present" }
```

> **Never rotate this value once tokens are live.** It decrypts the stored signing key; changing it
> makes that key unreadable and every existing session dies. Losing it is recoverable only by
> rotating to a brand-new signing key (everyone signs in again).

While you are here, confirm the provider keys are real and not `REPLACE_ME`:

```powershell
aws secretsmanager get-secret-value --secret-id $SECRET --query SecretString --output text | ConvertFrom-Json
```

---

## 1. Terraform

```powershell
cd C:\Users\googler\OneDrive\Desktop\Projects\pc-agent\v2\infra\environments\dev
terraform init
terraform validate
terraform plan
```

**What a correct plan looks like:** task-definition revisions for **accounts** and **model-proxy**
(new `AGENTD_AUTH_ISSUER`, the three `AGENTD_PUBLIC_*` vars on accounts, `AGENTD_AUTH_JWKS_URI` on
the proxy), one new `random_password`, and a new `platform_url` output. **No** database, EFS, ALB
or ECR replacement. If you see any resource being *destroyed*, stop and read it.

```powershell
terraform apply
terraform output platform_url
```

`platform_url` is the single address every client will bake. Keep it.

---

## 2. Ship the code

Push to `develop` (→ dev). The deploy workflow builds and rolls only what changed.

```powershell
cd C:\Users\googler\OneDrive\Desktop\Projects\pc-agent
git add -A
git commit -m "identity: token auth end to end"
git push origin develop
```

`v2/identity/**` and `v2/payments/**` are now in the workflow's path filter and in its build map,
so this rebuilds **accounts** and **model-proxy**. Before that fix a change confined to
`identity/` built nothing, reported success, and left the old images running — check the workflow's
"changed" step output lists both.

Watch it: <https://github.com/SaptarshiMazumder/pc-agent/actions>

---

## 3. Verify the platform is actually serving tokens

```powershell
$P = "<platform_url from step 1>"
curl.exe "$P/.well-known/agentd-platform"
```

Required: `"token_auth":true`, `"issuer"` equal to `$P`, and a non-empty `"jwks_uri"`.
`"token_auth":false` means `AGENTD_AUTH_ISSUER` did not reach the container — re-check step 1.

```powershell
curl.exe "$P/auth/jwks.json"                       # must list >= 1 key with a kid
curl.exe -X POST "$P/auth/login" -H "Content-Type: application/json" `
  -d '{\"email\":\"you@example.com\",\"password\":\"<8+ chars>\"}'
```

A real sign-in returns `access_token` + `refresh_token`. A `503` means no issuer; a `401` means the
account does not exist yet (`POST /signup` first).

---

## 4. Point the clients at it, once

```powershell
cd C:\Users\googler\OneDrive\Desktop\Projects\pc-agent\v2\clients\desktop
npm run sync:urls
git -C ..\..\.. add v2/clients/desktop/flavors
git -C ..\..\.. commit -m "flavors: point at the live platform"
git -C ..\..\.. push origin develop
```

This writes the live `platform_url` into every flavor. Everything else (accounts, model proxy, ws,
login providers) is **fetched at runtime**, so this is the only value that has to be right.

The web image gets the same address as a build arg — the deploy workflow passes
`VITE_AGENTD_PLATFORM_URL` automatically.

---

## 5. Cut installers

Only after step 4 is committed: an installer bakes its platform address permanently, and whatever
is in git at tag time is what every downloader talks to forever.

```powershell
# bump BOTH v2/agent_runtime/__init__.py and v2/clients/desktop/package.json to the same version
git tag v0.1.2
git push origin v0.1.2
```

The release workflow refuses to build if a flavor declaring `[platform]` has no `platform_url`.

---

## 6. End-to-end check

| Surface | Check |
| --- | --- |
| Web | open the app URL, sign up, sign in, send a message |
| Desktop | install the .exe, choose **Cloud**, sign in, send a message |
| Same account | sign in as the same email on both — `/me/credits` must show ONE balance |
| Billing | send a message, then `curl "$P/budget/<acct_id>"` with the internal key — `spent_usd` moves |
| Publishing | publish an agent from the desktop client; it authenticates with the same token |

The "same account" row is the one to actually perform. It is the requirement this whole change
exists for, and the only way it can fail now is a client baked against a different `platform_url`.

---

## Rollback

Each layer reverses independently:

- **Bad image** — the ECS circuit breaker rolls back automatically. Otherwise re-run the deploy
  workflow on the previous commit.
- **Bad Terraform** — `terraform apply` on the previous commit. No data is touched by this change.
- **Sign-in broken but the stack is healthy** — check `AGENTD_AUTH_ISSUER` first; it is the single
  setting that turns token auth on. Empty ⇒ `/auth/*` reports 501 and `/login` reports 503, by
  design, so the failure names itself.

There is no "fall back to the old session login": `sess_` credentials were removed
(see the plan's P5). Rolling back auth means rolling back the images.

---

## Known limits, stated plainly

- **Accounts must stay at `desired_count = 1`.** `/debit` is a read-modify-write with no row lock,
  so two tasks can both read the same balance and both succeed, silently overspending. This binds
  until accounts moves to Postgres — see the identity plan §11.
- **HTTP, not HTTPS**, until TLS is switched on. Tokens ride the WebSocket query string, so on the
  open internet they are visible to anything on the path. The 10-minute lifetime bounds the damage;
  it does not remove it. **Do not invite real users before TLS.**
- **No password reset.** Nothing sends email yet. A forgotten password needs a manual database fix.
