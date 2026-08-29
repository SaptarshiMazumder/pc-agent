# Bringing up a domain (thorgodofthunder.site → dev)

One-time sequence for pointing a freshly-bought domain at this environment. Everything is in
`dev.auto.tfvars` already (`root_domain`, `agent_hostnames`, `admin_hostname`); these are the
commands to make it real. **Run everything from this directory.**

## Why there are two applies

ACM proves you own a domain by looking up a DNS record in the zone — but the zone answers
nobody until the registrar's nameservers point at it. So: first apply creates the zone (and
stops when certificate validation can't complete yet), you flip the nameservers, second apply
sails through. This is a property of DNS, not of our terraform.

## Step 1 — create the zone, get the nameservers

```powershell
terraform init          # picks up the new us-east-1 provider alias
terraform apply -target=module.stack.aws_route53_zone.main
terraform output hosted_zone_name_servers
```

You get four names like `ns-123.awsdns-45.com`. (`-target` is normally a smell; here it is the
honest way to get the nameservers before anything waits on them.)

## Step 2 — point the registrar at Route 53 (the one manual step)

At the registrar where thorgodofthunder.site was bought: **Manage DNS → Nameservers → Custom**,
replace all entries with the four from step 1. Save.

Propagation is usually minutes for a fresh domain (nothing cached anything yet). Verify:

```powershell
nslookup -type=NS thorgodofthunder.site
```

When it returns the awsdns servers, continue.

## Step 3 — everything else

```powershell
terraform apply
```

This mints both wildcard certificates (ap-northeast-1 for the ALB, us-east-1 for CloudFront),
waits for ACM to validate them (a few minutes; the apply sits on
`aws_acm_certificate_validation` — that's the wait, not a hang), then: HTTPS on every listener,
host rules on :443, apex/wildcard/marketplace records, `AGENTD_APP_HOSTS` +
`AGENTD_APP_HOST_SUFFIX` on the daemon, `ADMIN_HOSTNAME` on the web task.

If step 2's propagation was slower than the 60m validation timeout, the apply errors — just run
`terraform apply` again once `nslookup` shows the awsdns servers.

## Step 4 — rebuild + redeploy the clients

The web image and desktop flavors bake URLs derived from `public_host`, which just changed from
the ALB hostname to the domain. Merge/push to `develop` (or run the Deploy workflow) so the
images rebuild against `https://thorgodofthunder.site`. Until then the old builds keep working
over the ALB hostname — the port listeners are all still there.

## Verify

| URL | Expect |
|---|---|
| `https://thorgodofthunder.site` | the web client |
| `https://admin.thorgodofthunder.site` | the admin console (sign-in gate) |
| `https://platform.thorgodofthunder.site` | Cloud Agent Builder |
| `https://marketplace.thorgodofthunder.site` | the marketplace |
| `https://<some-published-id>.thorgodofthunder.site` | that agent's app (or its install page) |
| `http://thorgodofthunder.site` | 301 → https |

## Swapping to the real domain later

Edit `root_domain`, the `agent_hostnames` keys, and `admin_hostname` in `dev.auto.tfvars`
(three values, same file), then repeat steps 1–4 for the new name. Nothing else references the
domain — that was the point.
