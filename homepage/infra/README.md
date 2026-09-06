# homepage infra

S3 + CloudFront. One private bucket, one distribution, origin access control
between them. Roughly 120 lines of Terraform and no compute at all.

**This is deliberately separate from `v2/infra`** — its own state, its own
lifecycle, no shared resources. The platform's `paused`/`hibernate` switches turn
off compute; a marketing site that goes dark when the backend sleeps is a funnel
that leaks, so this stands on its own and costs effectively nothing to leave up.

## Cost

Inside CloudFront's perpetual free tier (1 TB egress, 10M requests/month) plus a
few cents of S3 for a bundle under 1 MB. **Expect well under $1/month** at
ordinary traffic. There is no NAT, no load balancer, and nothing running.

## Apply

```bash
cd infra
terraform init
terraform plan            # read this before applying
terraform apply
```

First apply takes a few minutes — CloudFront distributions are slow to create.

Then publish:

```bash
cd ..
./scripts/deploy.sh
```

## Variables

Real values live in `homepage.auto.tfvars`, which is **gitignored** — the tracked
template is `homepage.auto.tfvars.example`. Same convention as `v2/infra`.

| Variable          | Default            | Notes                                                                     |
| ----------------- | ------------------ | ------------------------------------------------------------------------- |
| `region`          | `ap-northeast-1`   | Bucket region. Matches the platform; the distribution is global regardless. |
| `domain_name`     | `""`               | Empty ⇒ served on the `*.cloudfront.net` name.                             |
| `certificate_arn` | `""`               | ACM cert for `domain_name`. **Must be in us-east-1.**                       |
| `hosted_zone_id`  | `""`               | Route 53 zone for the alias record. Empty ⇒ no DNS is written.              |
| `price_class`     | `PriceClass_200`   | `_100` (NA+EU) is cheapest; `_All` adds APAC + South America.               |

There is **no `environment` variable**, and nothing here is named for one. This
site makes no API calls and reads no config — the built bundle is byte-identical
wherever it lands — so there is one site and one state, and an `-staging`/`-prod`
suffix would claim a distinction that does not exist. (Contrast `clients/web`,
which bakes `VITE_AGENTD_PLATFORM_URL` in at build time and genuinely is
per-environment.)

## The domain

**`homepage.thorgodofthunder.site`** — on the **apex** zone, not under `staging.`.

Everything it needs already exists, so this creates no zone and no certificate:

- **Zone** `thorgodofthunder.site` (`Z0018997SNXTBK9HGETY`) — the apex zone, which
  delegates `staging.` and `dev.` to their own zones. It is **not** owned by
  `v2/infra`'s staging state.
- **Certificate** — the us-east-1 cert for `thorgodofthunder.site` +
  `*.thorgodofthunder.site`, already ISSUED and attached to nothing.

That zone already has a wildcard `*.thorgodofthunder.site` A record. Route 53
answers from the most specific match and an **explicit record always beats a
wildcard**, so publishing this host's own A/AAAA alias is exactly what routes it
to this distribution. No existing record is modified.

To serve on the plain `*.cloudfront.net` name instead, blank out `domain_name`,
`certificate_arn` and `hosted_zone_id` — the site is live over https either way,
since a distribution ships with AWS's own certificate. Attaching or changing a
domain later is an **in-place update**: the distribution survives, so any URL
already shared keeps working.

## Caching

CloudFront uses the managed `CachingOptimized` policy, but freshness is really
decided by the `Cache-Control` headers `scripts/deploy.sh` sets at upload:

- `assets/*` — Vite hashes these filenames, so the bytes behind a name never
  change: `immutable`, one year.
- everything else — stable names that can change on any deploy: `must-revalidate`.

The deploy script uploads assets **first**, so the new `index.html` never lands
before the files it references.

## Teardown

```bash
terraform destroy
```

The bucket is `force_destroy = true` — every object is a rebuildable artifact
that `npm run build` reproduces.
