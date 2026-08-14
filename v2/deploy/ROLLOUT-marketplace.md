# Rollout: the public marketplace — do this once, in this order

Takes the marketplace change set live on the **dev** environment and ends with a real
Agent-Builder publish appearing in a public store page.

Run everything from PowerShell. Terraform state for dev is **local** (`infra/environments/dev`),
so run terraform from that folder.

**Prerequisites:** Docker Desktop running, AWS CLI logged in, `npm install` done once in
`v2/clients`.

---

## Why the order matters

`catalog.json` — the file the public page reads — is written by the code inside the **publish
Lambda**. Deploy the page before the Lambda and the store is permanently empty with no error
anywhere. So: infra → Lambda → seed → page → test.

---

## 1. Apply — the bucket and the CDN

```powershell
cd v2\infra\environments\dev
terraform apply
```

Expect ~7 new resources (site bucket, its policy and access block, OAC, cache/response policies,
distribution). **This apply sits for 5–15 minutes on the distribution** — CloudFront deploys to
every edge location before terraform returns. That is normal, not a hang.

**Success:**

```powershell
terraform output marketplace_url             # https://d1234abcd.cloudfront.net
terraform output marketplace_site_bucket
terraform output marketplace_distribution_id
```

Opening that URL now gives an error page — nothing is uploaded yet. Correct so far.

## 2. Rebuild the publish Lambda — **the step that makes publishing update the store**

The function is at `v4`, which predates the catalog. Everything else works without this and the
store never changes.

```powershell
cd v2
$REPO   = (terraform -chdir=infra\environments\dev output -raw publish_ecr_repository)
$REGION = ($REPO -split '\.')[3]

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin ($REPO -split '/')[0]
docker build -t "${REPO}:v5" -f services\publish\Dockerfile .
docker push "${REPO}:v5"
```

Then make it stick — edit `infra/environments/dev/dev.auto.tfvars`:

```hcl
publish_image_tag = "v5"
```

```powershell
cd v2\infra\environments\dev
terraform apply
```

**Success:** the plan shows exactly one change, `aws_lambda_function.publish` image_uri `v4` → `v5`.

## 3. Rebuild daemon + web

The daemon carries the runtime (its store now goes through the shared catalog builder); the web
image carries the refactored UI.

```powershell
cd v2\deploy\scripts
.\push-images.ps1 -Only daemon
.\push-images.ps1 -Only web
```

⚠️ The web image build is the one place a **fourth npm workspace** could bite: `clients/web/Dockerfile`
now copies `marketplace/package.json` because `npm ci` refuses a lockfile whose declared workspaces
are missing. If this step fails with *"workspace not found"*, that COPY line is the reason.

## 4. Seed the catalog for agents already published

Publishing writes `catalog.json` from now on, but everything already in the registry predates it.
One command, no signing key — the catalog is derived data, not signed.

```powershell
cd v2
$BUCKET = (terraform -chdir=infra\environments\dev output -raw registry_bucket)
..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle catalog --to "s3://$BUCKET"
```

**Success:** `uploaded catalog.json — N bundle(s), base https://agentd-dev-registry-….amazonaws.com/`

N should equal what the store used to show inside the app. `0 bundle(s)` means the registry is
genuinely empty, not that the command failed.

## 5. Deploy the page

```powershell
cd v2\deploy\scripts
.\deploy-marketplace.ps1
```

**Success:** it prints the URL. Open it — every agent from step 4 is on screen, in a browser with
no agentd, no sign-in and no session.

Empty page? Check the data before touching the CDN:

```powershell
curl.exe "$(terraform -chdir=..\..\infra\environments\dev output -raw marketplace_url)/catalog.json"
```

A 404 here means step 4 did not run or did not upload; JSON with bundles means the page is at
fault, not the registry.

---

## 6. The real test — publish from Agent Builder

### 6a. Run the desktop app against the cloud

```powershell
cd v2\clients\desktop
npm run dev
```

`predev` re-reads the terraform outputs into the flavors, so the app picks up `publish_url`
automatically. Sign in with the hosted account.

### 6b. Build and publish

In **Agent Builder**, make a small agent. Before publishing, put both doors in its `agent.toml`
so the store page has something to show:

```toml
[delivery]
web = true      # gives the card "Open in browser" (requires an [app] block)
exe = true      # gives it a Download — the engine is already pinned in dev.auto.tfvars
```

Press **Publish**.

**Expected on a first-ever publish: `202`**, "accepted, awaiting review." That is success, not
failure — the upload is parked and admission completes it.

### 6c. Admit yourself (once, ever)

```powershell
cd v2
$env:AGENTD_CREATORS_TABLE = (terraform -chdir=infra\environments\dev output -raw publish_creators_table)
..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle roster pending
..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle roster admit
```

**Success:** `admitted 1 creator(s). Published from parking: <agent> <version>.`

If `admit` reports no vaulted root key, do the one-time
`bundle roster upload-root` from **PUBLISH-SERVICE.md §5(b)** first — it is unrelated to this
change set.

Already admitted from earlier testing? Then 6b returns `200` and publishes immediately; skip 6c.

### 6d. Watch the store update itself

Reload the marketplace URL. The new agent is there — **with no deploy of anything**. That is the
whole design: the publish service rewrote `catalog.json`, and `catalog.json` is the store.

Click **Open in browser** and you land in the agent, running hosted.

---

## Verification checklist

- [ ] `terraform output marketplace_url` opens a store page
- [ ] it lists every agent the in-app Marketplace lists
- [ ] a fresh Agent-Builder publish appears there after a reload, with no redeploy
- [ ] **Open in browser** reaches a working agent
- [ ] **Download** starts an installer download
- [ ] the in-app Marketplace (desktop) still looks and behaves exactly as before

## Known and expected

- **"Not secure" after clicking Open in browser.** The store is https (CloudFront); the daemon is
  http (the ALB has no certificate). A top-level navigation from https to http is allowed — it
  just says so. Fixed when the ALB gets TLS, not before.
- **The URL is `d1234abcd.cloudfront.net`.** A domain is two variables and an in-place apply,
  whenever you want it — see MARKETPLACE.md §4.
- **No sign-in on the store.** Deliberate: an https page calling the http ALB is mixed content and
  is blocked outright. Browsing and downloading need neither.
