# The public marketplace — command card (you run every command)

**Goal state:** a stranger opens a URL, browses every published agent, and either opens one in
their browser or downloads an installer — with no account, no agentd, and nothing to install
first. No new service, no container, ~$0/month.

Sibling of `LAUNCH.md` (the platform) and `PUBLISH-SERVICE.md` (how agents get into the store).
This card only puts a storefront in front of what those two already produce.

> **No certificate needed.** Unlike everything else here, the marketplace is not behind the ALB.
> It is a CloudFront distribution, and a distribution's own `*.cloudfront.net` address comes with
> AWS's certificate — so it is live over **https** on the first apply. A domain is Step 4 and is
> optional forever.

---

## How it works, in one paragraph

Publishing already writes two files to the registry bucket: `index.json` (the signed record every
installed client verifies) and `catalog.json` (the same listing with creator names resolved off
the signed roster and every url made absolute). The marketplace page is a static bundle that
fetches `catalog.json` and draws cards. One CloudFront distribution serves the page from a site
bucket and `catalog.json` from the registry bucket, so the browser sees a single origin and there
is no CORS anywhere. **Publishing an agent updates the store with no deploy here.**

---

## 0. See it with no AWS at all

The page is the same bundle locally and in the cloud. Point it at any directory registry:

```powershell
cd v2
$REG = "$env:TEMP\agentd-registry"
..\.venv\Scripts\python.exe -m agent_runtime.cli.main bundle publish agents\game-master --to $REG --unsigned
```

**Success:** the publish prints `wrote index.json` **and** `wrote catalog.json`.

```powershell
cd v2\clients
$env:AGENTD_REGISTRY = "$env:TEMP\agentd-registry"
npm run dev:marketplace          # http://localhost:5274
```

**Success:** the store renders your agent's card. If it says the registry is empty, `AGENTD_REGISTRY`
is not pointing at a folder containing `catalog.json` — the dev server logs a warning naming it.

This is the whole product minus hosting, so do it first: it is where a broken card is cheap to see.

## 1. Apply

The bucket and the distribution come up with everything else — no separate environment, no
two-step bring-up (there is no image to push).

```powershell
cd v2\infra\environments\dev
terraform apply
```

**Success:**

```powershell
terraform output marketplace_url              # https://d1234abcd.cloudfront.net
terraform output marketplace_site_bucket
terraform output marketplace_distribution_id
```

A distribution takes a few minutes to finish deploying. Until it does the URL answers, but may
answer from only some edge locations.

## 2. Put the page online

```powershell
cd v2\deploy\scripts
.\deploy-marketplace.ps1
```

Build, sync, invalidate. That is the entire deploy — there is no image, no ECS service and no
rollout to wait on.

**Success:** it prints `Marketplace deployed: https://…`. Open it. You should see every agent the
registry lists.

Seeing an EMPTY store on a registry that has bundles? Fetch the catalog directly:

```powershell
curl.exe "$(terraform -chdir=..\..\infra\environments\dev output -raw marketplace_url)/catalog.json"
```

A `404` means nothing has published since this change shipped — `catalog.json` is written by the
publish path, so the fix is to publish once (or re-run `bundle publish`), not to touch the CDN.

## 3. Check the three doors

Each card renders exactly the doors its author opened, which is worth confirming once:

| Door | Shows when | Needs |
| --- | --- | --- |
| **Open in browser** | the author set `[delivery] web = true` **and** the registry knows a hosted deployment | nothing — it links to the daemon's `/apps/<id>/` |
| **Download** | the publisher shipped an installer for the visitor's OS | nothing — a plain S3 link |
| *(neither)* | — | the card says which of the two it is, rather than showing nothing |

**Install is deliberately absent here.** It needs a daemon, and this page is for people who do not
have one. The same cards inside the app render it (they share one component).

## 4. A domain — optional, whenever you want one

Two variables, one apply, and the distribution is updated **in place**: the `cloudfront.net` URL
keeps working and nothing already shared breaks.

```hcl
# infra/environments/dev/dev.auto.tfvars
marketplace_domain_name     = "agents.example.com"
marketplace_certificate_arn = "arn:aws:acm:us-east-1:…"
```

⚠️ **The certificate must be in `us-east-1`**, whatever region this deployment runs in — CloudFront
reads them from nowhere else. This is NOT the same certificate as `certificate_arn` (that one is
regional, for the ALB). Pasting the regional ARN is rejected with a message naming the region, so
the mistake fails at plan time rather than at apply.

Then point DNS at the distribution: an ALIAS (Route 53) or CNAME record from your hostname to the
host part of `marketplace_url`.

---

## Rolling out a change to the page

```powershell
.\deploy-marketplace.ps1
```

Same command every time. The invalidation only ever touches `/index.html`; every other asset
carries a content hash in its filename, so a new build writes new names and there is nothing
stale to purge.

## What this deliberately does NOT do

Listed so each is a decision rather than an oversight:

- **No sign-in, no purchases.** An https page calling the HTTP-only ALB is mixed content and the
  browser blocks it outright. That is the point at which the ALB needs TLS — and it is not needed
  for a store that browses and downloads. Open-in-browser still works today: a top-level
  navigation from https to http is not blocked, it just shows "Not secure".
- **No per-agent pages or SEO.** A crawler gets one page. The fix is static: have the publish
  service also write `/a/<id>/index.html` per bundle. No server either way.
- **No ratings, reviews or download counts.** Those are the first features here that need
  *writable* state, so they are a small sibling Lambda + DynamoDB (the publish service's shape),
  never a container.

## Cost

CloudFront's free tier is 1 TB out and 10M requests a month, permanently; the site bucket holds a
few hundred KB. Installer downloads do **not** go through the distribution — the catalog links
straight to the bucket, which is what the desktop client has always done.

Deliberately NOT switched off by `paused` or `hibernate`. Those turn off compute; a storefront
that goes dark whenever the backend sleeps is a funnel that leaks, and this one costs nothing to
leave standing.

## Teardown

```powershell
terraform destroy -target=aws_cloudfront_distribution.marketplace
```

The registry bucket is untouched — the store is a view of it, never its owner.
