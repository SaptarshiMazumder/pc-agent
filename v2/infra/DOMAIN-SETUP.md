# Domains — one subdomain per environment

The namespace, after this migration:

```
thorgodofthunder.site              nobody's. Holds the NS delegations below; serves nothing.
├── dev.thorgodofthunder.site      the dev web client
│   ├── platform.dev.…             Cloud Agent Builder
│   ├── admin.dev.…                the admin console
│   ├── marketplace.dev.…          the marketplace (CloudFront)
│   └── <agent-id>.dev.…           every published agent
└── staging.thorgodofthunder.site  the same five things, for staging
```

Each environment owns its own Route 53 zone, its own pair of ACM certificates (one in the ALB's
region, one in us-east-1 for CloudFront) and its own wildcard. An environment's namespace is a
subtree nothing else reaches into, so the two can never fight over a record, and neither can
take the other's HTTPS down.

**A wildcard certificate covers exactly one label.** `*.dev.thorgodofthunder.site` covers
`platform.dev.…` and every `<agent>.dev.…`, and nothing deeper. That single fact is why the
environment name is the *first* label — `platform.dev.…`, not `dev.platform.…`, which would
need a third certificate for a subtree nobody owns.

**The registrar is not involved.** These are subdomains of a zone we already control, so
delegation is an NS record we add ourselves. Nameservers at the registrar never change.

## What this replaced, and the two traps in it

Dev used to own the **apex** (`root_domain = thorgodofthunder.site`), and staging had no zone at
all — it borrowed dev's wildcard certificate by ARN and had one hand-made A record. That left
two things that will bite whoever applies this without reading:

1. **Dev's terraform owns the apex zone as a resource.** Changing `root_domain` therefore reads
   as "destroy that zone, create a different one" — which would take the whole domain down,
   staging included, and delete the marketplace record. Step 1 exists to prevent exactly this
   and is not optional.
2. **`*.auto.tfvars` beats a variable default.** The new hostnames are defaults in each
   environment's `main.tf`, so a stale `root_domain = "thorgodofthunder.site"` line in a local
   gitignored tfvars silently keeps that environment on the apex and nothing appears to happen.

## Step 0 — the tfvars line (both environments, whoever holds the file)

In `environments/dev/dev.auto.tfvars` and `environments/staging/staging.auto.tfvars`: delete any
`root_domain`, `agent_hostnames` or `admin_hostname` line. The values now live in `main.tf`
where the whole team can see them. Everything else in those files stays.

## Step 1 — hand the apex zone over (dev only, BEFORE any apply)

Run from `environments/dev`, on the machine that holds dev's real state. This removes the apex
zone, its records and its certificates from terraform's *bookkeeping*. Nothing is deleted in
AWS: they keep serving traffic, terraform just stops believing it owns them.

```bash
terraform state list | grep -E "route53|acm_certificate"     # see exactly what is there
```

Then `terraform state rm '<address>'` for each line that comes back — the zone, the apex /
wildcard / marketplace records, both certificate-validation record sets, both certificates and
both validations. Quote the addresses: several contain `[` `]`.

Sanity check before moving on: `terraform plan` should now propose **creating** a zone and
certificates for `dev.thorgodofthunder.site`, and should propose **destroying nothing** in
Route 53 or ACM. If it wants to destroy a zone, stop — something is still tracked.

> The old wildcard certificate must **survive** this step: staging's listeners are still using
> it until step 3. That is why this is `state rm` and not `terraform destroy`.

## Step 2 — dev's zone, and its delegation

```bash
cd environments/dev
terraform init                                               # picks up the us-east-1 alias
terraform apply -target=module.stack.aws_route53_zone.main    # create the zone alone
terraform output hosted_zone_name_servers                     # four ns-….awsdns-… names
```

In the **apex** zone (`thorgodofthunder.site`, Route 53 console or CLI) create:

| Name | Type | Value |
|---|---|---|
| `dev.thorgodofthunder.site` | `NS` | the four names above, one per line |

Then the real apply:

```bash
terraform apply
```

It mints both certificates and **waits** on `aws_acm_certificate_validation` until ACM can read
its DNS proof through that delegation. Minutes, usually. That pause is the design, not a hang;
if the delegation was late and the 60-minute timeout fires, just run `terraform apply` again.

## Step 3 — staging's zone, and its delegation

Same sequence from `environments/staging`, with one extra deletion first: the apex zone contains
a hand-made `staging.thorgodofthunder.site` **A** record from the old setup. Delete it when you
add the NS record — a parent zone must not both delegate a name and answer for it.

```bash
cd environments/staging
terraform init
terraform apply -target=module.stack.aws_route53_zone.main
terraform output hosted_zone_name_servers
# apex zone: delete the staging A record, add staging.thorgodofthunder.site NS -> those four
terraform apply
```

This is also the apply that gets staging off dev's certificate and onto its own.

## Step 4 — everything with a URL baked into it

Every environment's public host just changed, so anything that baked one is stale:

```bash
cd v2/clients/desktop
npm run sync:urls -- --env dev        # or --env staging
```

- **Web image**: its API origins are build args (`.github/workflows/deploy.yml`). Rebuild both
  environments' images, or the page loads over https and its first request goes somewhere that
  no longer answers.
- **Payment webhooks** — the rails hold absolute URLs and will keep POSTing to the old ones:
  - Dodo (staging) → `https://staging.thorgodofthunder.site:4100/payments/webhook`
  - Razorpay (dev) → `https://dev.thorgodofthunder.site:4100/payments/webhook`
- **Marketplace**: now `marketplace.dev.…` / `marketplace.staging.…`.
- **Published agents**: now `<agent-id>.dev.…`. Old links stop resolving.

## Step 5 — cleanup, in this order

Only once **both** environments are applied and verified:

1. In the apex zone, delete the leftover `A` records for `thorgodofthunder.site` and
   `*.thorgodofthunder.site` (both still point at dev's ALB), plus the old ACM validation
   CNAME. Keep the two `NS` delegations and the zone itself. The bare apex then resolves to
   nothing, which is the intent — it is not any environment's front door.
2. Delete the old wildcard certificate (`*.thorgodofthunder.site`) **last**, after confirming
   no listener references it. Before staging's apply it is load-bearing.

## Verify

Per environment, substituting `dev` / `staging`:

| URL | Expect |
|---|---|
| `https://<env>.thorgodofthunder.site` | the web client |
| `https://platform.<env>.thorgodofthunder.site` | Cloud Agent Builder |
| `https://admin.<env>.thorgodofthunder.site` | the admin console (sign-in gate) |
| `https://marketplace.<env>.thorgodofthunder.site` | the marketplace |
| `https://<published-id>.<env>.thorgodofthunder.site` | that agent's app, or its install page |
| `http://<env>.thorgodofthunder.site` | 301 → https |
| `https://<env>.thorgodofthunder.site:4100/health` | the accounts service |
| `thorgodofthunder.site` | nothing (NXDOMAIN/no answer) — deliberate |

## Changing the domain later

`root_domain`, the `agent_hostnames` keys and `admin_hostname` in that environment's `main.tf` —
three values, one file — then repeat steps 2–4 for the new name. Nothing else in the repo
references the domain; that is the point of the design.
