# Agentd single server production cost and replacement plan

Updated October 12, 2026. This supersedes the two-server sizing in [the complete resource inventory](CHEAP-PRODUCTION.md) for the user's requested scope: one production machine, no staging, no GPU rentals, and model/API charges excluded from the estimate.

## Monthly cost

| Item | Monthly amount before tax | Assumption |
| --- | ---: | --- |
| OVH VPS-3 | US$14.50 | Singapore Linux x86_64, 6 vCPU, 12 GB RAM, 100 GB NVMe, monthly billing |
| IPv4, IPv6, daily provider backup | Included | Standard included plan features |
| R2 storage | US$1.35 | 100 GB total retained across public registry, private submissions, scratch and encrypted backups; first 10 GB free |
| DNS and TLS | US$0 hosting charge | Cloudflare Free DNS and automated Let's Encrypt certificates |
| PostgreSQL | Included in server | Move accounts from Neon; no paid external database retained |
| Application, publishing, worker controller, schedules and local monitoring | Included in server | Shared server resources and open-source software |
| Container registry | Currently US$0 | GitHub Container Registry |
| CI and artifact storage | US$0 within allowance | Existing GitHub account allowance; charge overages separately |
| **Specified infrastructure subtotal** | **US$15.85** | R2 requests stay within free allowances |
| **Working budget** | **US$20–25** | Allows modest storage/request growth; before tax and existing domain/transaction charges |

The [OVH Asia USD catalog](https://ca.api.ovh.com/1.0/order/catalog/public/vps?ovhSubsidiary=ASIA) gives US$12.32/month equivalent with annual prepayment: US$147.84 upfront for the server. Adding the same R2 storage yields US$13.67/month equivalent. Start monthly until the actual host passes compatibility and load checks.

The latest stock check in this turn shows VPS-3 Linux **available in Singapore**, after the preceding check had shown it unavailable. Stock remains subject to change. [Availability API](https://ca.api.ovh.com/1.0/vps/order/rule/datacenter?ovhSubsidiary=ASIA&planCode=vps-2027-model3).

[R2 Standard pricing](https://developers.cloudflare.com/r2/pricing/) is US$0.015/GB-month beyond 10 GB, with one million writes and ten million reads included monthly. Operations exceeding those allowances cost extra. Store 500 GB instead of 100 GB and the subtotal becomes US$21.85/month. Requests, storage retention and CI use must be capped/monitored.

The complete non-model, non-GPU bill is:

`US$15.85 + domain renewal costs allocated monthly + payment transaction fees + storage/request/CI overages + applicable tax`

Domain registrations already exist, but their renewal invoices were not available in the infrastructure inventory. Payment fees depend on transactions. They cannot truthfully be included as zero or replaced with an invented fixed amount. At zero payment transactions, within usage allowances, and ignoring domain renewals paid separately, the hosting subtotal is US$15.85 before tax. If 9% tax applies to all of that subtotal, it is approximately US$17.28; the actual account's tax treatment decides the invoice.

Relative to the previous two-server specification, removing the US$10 worker saves US$10/month. Relative to the historical September AWS infrastructure bill of US$195.33 pretax, US$15.85 is US$179.48/month lower, about 92%. The older bill included staging and cannot be treated as the current stopped-fleet bill or a production-only comparison.

## What runs on the one machine

Run the reverse proxy, web/admin UI, accounts/identity/payments, model proxy, agentd, telemetry ingest, PostgreSQL, publishing/signing controller, worker controller, schedule runner and bounded monitoring on this host. These are separate services/containers sharing one machine, not additional server purchases.

Run builds, tools, browser/MCP sessions and installer compilation in disposable isolated sandboxes managed by the worker controller. Begin with **one active sandbox job/session at a time**, queued additional work, a 2 GB job limit and reserved memory for accounts, database and daemon. A persistent browser/MCP session consumes that capacity. This is a starting capacity assumption; memory peaks and integration compatibility must be measured. If that queue is inadequate, increase host resources or reinstate the worker.

The single-server change alters the security architecture: untrusted execution now shares the physical host holding tenant data. The existing deployment deliberately uses a separate execution boundary. Plain Docker or the development HTTP runner is not an equivalent replacement. A gVisor integration, strict job network/filesystem isolation, no credentials in jobs, host resource limits and acceptance checks are required before this setup is usable for customers. It does not retain the separate-host boundary from the two-server plan. Hardware microVM requirements would need a KVM-capable host and a separately priced deployment.

A single machine also has one failure domain. Independent encrypted backups permit recovery; they do not provide uninterrupted operation during host failure.

## Replace and retire

Retire old resources only after their replacements work and recoverable data/signing material have been migrated.

| Existing component | Replacement or disposition |
| --- | --- |
| EC2 pools, ECS services, Fargate task infrastructure and cluster | One OVH host with production manifests and supervised containers; disable old watchdog/autoscaling before final teardown |
| ALB, target groups and listeners | Caddy/nginx on the OVH host, with HTTPS/WSS and host/path routing |
| Service discovery, VPC endpoints and AWS network rules | Internal container DNS and restricted host networks/firewall rules |
| EFS and application volumes | Persistent main-host directories, preserving tenant layout and encryption, plus independent backups |
| Neon accounts database | Self-host PostgreSQL with imported data, recovery tooling and restricted roles; stop paid Neon compute only after validation |
| S3 registry, marketplace and scratch buckets | R2 public registry, private submissions, private scratch and private backup buckets; serve static marketplace from the host or existing static hosting |
| Marketplace CloudFront distribution | R2 custom domain/CDN for public package assets and static hosting for the storefront |
| Eight ECR image repositories | Private GHCR images with release digests and bounded retention |
| Publish Lambda | Production HTTP publish/signing service; preserve approved ownership and signature behavior |
| Builder/executor Lambdas and Fargate dispatcher | Local trusted controller launching isolated jobs, including persistent browser/MCP sessions and cancellation |
| DynamoDB creators, bundle owners and publish locks | PostgreSQL schemas with equivalent conditional writes/atomic locking |
| DynamoDB sandbox leases | Atomic PostgreSQL admission leases with expiry and limits appropriate to one machine |
| AWS Secrets Manager | Scoped local secret storage and startup injection; admin secret rotation/restart adapters must change |
| KMS-wrapped publisher material | Decrypt recoverable signing records with the existing KMS key, then re-encrypt under the replacement vault; preserve signing key bytes/trust anchors |
| EventBridge/Scheduler and scheduled-job Lambda | Host timers invoking authenticated billing endpoints, with execution records, retries and missed-run alerts |
| ECS host watchdog | Host process supervision and alerts; its AWS scheduler/Lambda become unnecessary |
| CloudWatch log groups, EMF metric extraction, alarms and dashboards | Rotated local logs, metric extraction, capped metrics and independent alert delivery |
| Route 53 staging/old zones | Preserve needed records in Cloudflare, update delegation, then retire unused zones |
| Old IAM roles/policies and infrastructure state | Retain while exporting/migrating, then remove only roles/state resources belonging to the retired stack |

The AWS-specific publishing, storage, worker and admin adapters require code changes. Accounts can already use environment secrets with `AGENTD_APP_SECRET_ID=local`; keep its PostgreSQL requirement enabled. The four-service Compose file is not the complete production manifest.

The no-GPU scope removes Vast rental credentials, funding and `/vast/reap` scheduling from the new deployment. Disable platform GPU rental controls and do not provision Vast machines or Comfy GPU hosting. Libraries can remain installed; features requiring a GPU will not be operational without a user-supplied external machine or a separately funded service. Paid model/media API usage remains outside this estimate by user request.

## Keep

| Keep | Required adjustment |
| --- | --- |
| Existing domain registrations and product names | Change DNS destinations/delegation; do not buy new domains |
| Existing GitHub repository and source | Rewrite AWS deployment jobs to deploy the new host/GHCR images |
| Application behavior and modules | Keep agentd, accounts, identity, payments, model proxy, web/admin UI, ingest, marketplace and publishing logic |
| Accounts, balances, ledger, identity and payment history | Migrate intact; do not reset the database to reduce costs |
| Tenant files, sessions, installed bundles and encrypted credential data | Move intact with their recovery keys |
| JWT/publisher signing material and public trust anchor | Preserve bytes and wrapping keys until successful migration; changing them is a separate rotation |
| Current payment provider | Keep Dodo and its verified webhook semantics; update public URLs; transaction fees remain usage costs |
| Google login and integrations | Keep client accounts/consent setup; update exact callback and return URLs |
| Model/API provider accounts | Keep the enabled routes and credentials; their bill is excluded from this estimate |
| Billing schedules and money-related monitoring | Move their execution/alerts; maintain renewal, expiry, snapshot and debit correctness |
| Desktop release and installer functionality | Rebuild with production endpoints, registry addresses and matching publisher trust |

## Confirmed AWS remnants and teardown order

The latest inventory found the following agentd staging resources still present:

- Eight repositories: `agentd-staging/{accounts,builder,daemon,executor,ingest,model-proxy,publish,web}`.
- Five enabled schedules: `agentd-staging-{ledger-snapshot,subscription-renewals,ecs-host-watchdog,vast-reap,close-expired-credits}`.
- Two Lambdas: `agentd-staging-scheduled-jobs` and `agentd-staging-ecs-host-watchdog`.
- Three S3 buckets ending `274ab2e9`: staging registry, marketplace and builder scratch; the preceding object inventory showed them empty.
- CloudFront distribution `E232VU1E4ZHPGA`, identified by its comment/origins as the staging marketplace.
- Staging creator, bundle-owner and lock tables, staging EFS, app secret and enabled publish KMS key; the app secret points to Neon.
- `/agentd/staging` and the two staging Lambda log groups, along with existing alarm/dashboard resources to audit during teardown.
- Old Route 53 zones and the staging ECS cluster.
- A running staging `t3.medium`, `i-04bf4a0b8d1cb55af`, in Auto Scaling Group `agentd-staging-ecs` (minimum 0, desired 1, maximum 2), using the staging ECS instance profile. Its attached 30 GB gp3 root disk is `vol-061ccfde00d24e337`. The second group, `agentd-staging-ecs-daemon`, is at zero capacity. Stop the watchdog and scaling machinery before retiring the instance so it is not recreated.

Two Elastic IP allocations, `eipalloc-0f8c851e2f0794dca` and `eipalloc-0a8e6f36f6cf39bc8`, belong to AWS-managed network interfaces described as `ELB app/agentd-staging/c5e7b2c0da217a55`. Retire the owning staging load balancer through its normal API and verify its managed IPs are released; do not manually tear off managed interfaces. The checked regions have no remaining VPC endpoints. The instance and ALB-managed interfaces appeared after the preceding empty-compute inventory; live state is changing, so check again immediately before teardown.

The old production publish KMS key is pending deletion on **October 17, 2026 at 12:10 JST**. Establish whether any surviving signing records depend on it before that deadline. Export signing material by decrypting records, not by assuming the managed KMS wrapping key is exportable.

1. Inventory and back up the Neon database, signing records/keys and remaining tenant data.
2. Disable retired watchdog/autoscaling and staging schedules so they cannot keep invoking or recreate retired infrastructure.
3. Provision the replacement host/storage, complete adapters and restore data.
4. Pass login, billing, isolation, publishing, installer and backup-restore acceptance checks from the complete resource inventory.
5. Update client releases and DNS, then validate production traffic.
6. Retire the old AWS buckets/CDN/images/data stores/logs/roles and the old Neon deployment after migration verification. Preserve unrelated resources and domain registrations.

This is a cost and migration plan. No AWS deletion, server order, DNS change or key rotation has been performed.
