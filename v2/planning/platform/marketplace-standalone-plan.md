# Marketplace as its own page

**Goal.** Anyone can visit the marketplace, browse agents, and get one — without installing
agentd, signing in, or knowing what a daemon is. Today browsing is a method on the daemon
(`marketplace.catalog`), so the store only exists for people who already bought the product.

**Two rules this plan obeys:**

1. **No new service.** The write side (publish Lambda) already exists and is the only piece that
   earned a server — it holds the signing key. Browsing reads one public JSON file; a server in
   front of that adds cost and an outage mode to something S3 already does perfectly.
2. **No meaningful new cost.** ~$0–2/month. CloudFront's free tier covers 1 TB out and 10M
   requests; S3 for a few MB of static files is cents.

---

## The shape

```
CloudFront (free https on d1234.cloudfront.net — no cert needed)
├── /            → S3 site bucket      the marketplace page
├── /catalog.json→ S3 registry bucket  what to render
├── /bundles/*   → S3 registry bucket  the .agentpkg files
└── /installers/*→ S3 registry bucket  the .exe files
(later) /api/*   → ALB                 accounts, only when we add sign-in
```

Everything under one domain, so the page never makes a cross-origin request and we never touch
CORS. A custom domain later is two added lines on the same distribution — not a rebuild.

**Three doors on every card**, exactly as today, minus the one that needs a daemon:

| Door | Works for | Needs |
| --- | --- | --- |
| Open in browser | anyone | nothing — links to the hosted daemon's `/apps/<id>/` |
| Download installer | anyone | nothing — a plain S3 link |
| Install | people running agentd | a daemon, so it renders only inside the app |

---

## Phase 1 — Move the catalog join to publish time *(local, no AWS)*

Right now `_entry_dict` in `marketplace_service.py` builds each store row on every daemon on every
catalog call: look the publisher's name up in the signed roster, make installer URLs absolute,
stamp `webUrl`. Same answer every time.

Do it **once, when a bundle is published**, and write `catalog.json` next to `index.json`.

- `infrastructure/marketplace/index_builder.py` — emit `catalog.json` (finished rows).
- `marketplace_service.catalog()` — read those rows, add only what the daemon actually knows:
  `installed`, `installedVersion`, `updateAvailable`, `compatible`.
- The publish Lambda already imports `index_builder`, so it gets this free.

**Why this first:** it's the whole decoupling. After it, the daemon is no longer the thing that
knows how a store looks, and any client can render one.

**Not moving:** `trust.py`. The page renders links; it never runs a bundle. Verification stays
where execution is — the daemon on install, the stub on download. A browser checking a signature
it then ignores is theater.

## Phase 2 — Split the view *(local, no AWS)*

`MarketplaceView.tsx` stops calling the gateway itself and takes its rows from a **provider**:

- inside the app → `marketplace.catalog` RPC, renders Install/Uninstall
- on the public page → `fetch('/catalog.json')`, renders Open / Download / Get the app

Card rendering, search, and badges are shared — one component, two sources. New entry point at
`v2/clients/marketplace/` (vite, reuses `@agentd/ui`), same workspace as `web` and `desktop`.

**After phase 2 the whole marketplace runs on `npm run dev` against a local directory registry.
Nothing has touched AWS yet.**

## Phase 3 — Put it online *(~1 hour of AWS)*

- `infra/modules/marketplace.tf` — one S3 bucket, one CloudFront distribution, the four behaviors
  above. **No `aliases`, no certificate.** Live on the `cloudfront.net` URL.
- A deploy step: `aws s3 sync` + one invalidation. No image, no ECS, no rollout.

## Phase 4 — Domain, whenever you feel like it

Buy a domain, request a free ACM cert **in us-east-1** (CloudFront only accepts that region — the
ALB's cert lives in the service region, so it's two certs), add `aliases` + `viewer_certificate`.
In-place update; the URL you were already using keeps working.

---

## How it takes new features without growing

Sort every future feature by **who writes the data**:

| Feature | Where it goes | New infra |
| --- | --- | --- |
| Categories, tags, screenshots, changelogs, featured/curated lists | fields in `bundle.toml` → `catalog.json` | none |
| Search, filters, sorting, detail pages | the static page, client-side | none |
| SEO / shareable links | publish writes `/a/<id>/index.html` per bundle | none |
| Ratings, download counts, reviews | writable public state → sibling Lambda + DynamoDB, same pattern as publish | ~$0 idle |
| Purchases, library, entitlements | `accounts` — it already knows who the user is | none |

The rule: **anything computable at publish time is a file, not an endpoint.** That's what keeps a
marketplace with a hundred features running on a bucket.

---

## Order

1. `catalog.json` in `index_builder.py` + daemon reads it — local
2. Provider split in `MarketplaceView` + `clients/marketplace` entry — local
3. `marketplace.tf` + sync step — AWS, no cert
4. Domain + cert — whenever

1 and 2 are the real work and need no AWS at all.
