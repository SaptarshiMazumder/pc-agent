# Agentd production resources and migration plan

Updated October 12, 2026. One production environment, with all cloud features enabled and no staging environment.

The user's later single-machine, no-GPU scope is priced in [Single server production](SINGLE-SERVER-PRODUCTION.md). Use that plan for the current purchasing decision; the two-server configuration below remains the separate-worker alternative and complete feature inventory.

The proposed setup uses two Linux servers: a 12 GB main server for the trusted application and database, and an 8 GB worker for isolated builds, tools, browser sessions and MCP sessions. Cloudflare supplies DNS and object storage; GitHub supplies container images and releases. Budget US$30–40 per month before tax for a small deployment after the migration work below. Model calls, GPU rentals, payment fees, domain renewals and CI overages are additional usage costs.

This is a starting capacity with a queue, not unlimited concurrent execution or automatic failover. The worker controller, publishing adapters and admin controls need implementation before this setup has feature parity. Server prices alone do not establish that the application is ready to deploy.

## Server resources

| Resource | Proposed location | Starting specification | Monthly price before tax |
| --- | --- | --- | ---: |
| Main server | OVH Singapore, VPS-3 | Linux x86_64, 6 vCPU, 12 GB RAM, 100 GB NVMe | US$14.50 |
| Worker server | Same region, OVH VPS-2 | Linux x86_64, 4 vCPU, 8 GB RAM, 75 GB NVMe | US$10.00 |
| Public IP addresses | One per server | Included IPv4 and IPv6; worker control remains private | Included |
| Provider backups | Both servers | Included daily backup retaining the preceding recovery point | Included |
| Object storage and independent backups | Cloudflare R2 Standard | Initially budget 100 GB total retained across buckets | US$1.35 storage, plus operations beyond free allowances |
| DNS | Existing domains on Cloudflare Free | Apex, admin, product and wildcard records | US$0 additional hosting charge |
| TLS | Let's Encrypt at the reverse proxy | Automated certificate renewal; DNS challenge for wildcard | No certificate purchase |
| Container image storage | GitHub Container Registry | Private images, pinned release digests, bounded retention | Currently free |
| CI and engine releases | Existing GitHub repository | Hosted runners; keep artifacts within the account allowance | US$0 within allowance; overages variable |
| Monitoring and scheduling software | Main server and host timers | Local logs, metric extraction, alerts, independent uptime probe | Included in server capacity; external services depend on chosen allowance |
| **Identified server and 100 GB storage subtotal** | | | **US$25.85** |
| **Operating budget** | | Extra storage/operations and modest contingency | **US$30–40** |

OVH's [Asia USD catalog](https://ca.api.ovh.com/1.0/order/catalog/public/vps?ovhSubsidiary=ASIA) currently quotes these monthly prices with no commitment. Annual prepayment reduces the pair to US$20.82 per month equivalent, paid US$249.84 for the year before tax. Start monthly until compatibility and usage are established. Currency, tax and eligibility depend on the billing account.

The October 12 live availability API reports the 8 GB and 12 GB Linux plans out of stock in Singapore for both SG and ASIA subsidiaries. The 4 GB plan remains available in Singapore under SG. This differs from the October 10 check. Treat the specifications and prices above as a procurement target; do not assume that pair can be ordered today. Availability must be checked again at purchase: [VPS-2](https://ca.api.ovh.com/1.0/vps/order/rule/datacenter?ovhSubsidiary=SG&planCode=vps-2027-model2), [VPS-3](https://ca.api.ovh.com/1.0/vps/order/rule/datacenter?ovhSubsidiary=SG&planCode=vps-2027-model3).

The existing production design targets Indian users in Mumbai. Singapore is the proposed compromise for Asian latency and cheap hosting. Put both servers in one region and measure India-to-Singapore latency. Europe is an alternative when Asian capacity is unavailable, but adds latency to interactive requests; a CDN does not remove that backend round trip.

The [OVH Singapore plan conditions](https://www.ovhcloud.com/en-sg/vps/vps-singapore/) give VPS-2 and VPS-3 a 1 TB monthly traffic quota each, after which throughput is capped at 10 Mbps. Serve large package and installer downloads from object storage rather than the main server.

## Main server services

These are containers or host processes sharing the main server, not separately purchased servers.

| Service | Purpose and required connections | Starting memory allocation |
| --- | --- | --- |
| Reverse proxy | HTTPS, WebSockets, host routing, upload limits, forwarded origin handling; Caddy or nginx | 256–512 MB |
| Web client and admin UI | Existing web build; separate admin hostname | 256 MB |
| Agentd daemon | Tenant sessions, agents, files, orchestration, memory, scheduled agent activity and model brokering | 3 GB initially; measure actual peaks |
| Accounts | Identity/JWT/JWKS, sessions, accounts, credits, ledger, products, payment callbacks and GPU rental control | 1 GB |
| LiteLLM model proxy | Provider credentials, authentication, streaming and usage billing | 1 GB |
| Ingest | Opt-in desktop/browser telemetry with current allowlists and redaction | 256–512 MB |
| PostgreSQL | Accounts, identity, payments, GPU rental records; new publishing and worker-control schemas | 1–2 GB initially |
| Publish API and signing controller | Authenticated publishing, creator administration, signed index and installer orchestration | 512 MB–1 GB; heavy compilation goes to bounded jobs |
| Schedule runner | Billing endpoints, GPU cleanup, backup triggers and health reporting | Small host process or container |
| Monitoring | Rotated JSON logs, EMF metric parser/exporter, metrics, alerts and health checks | 512 MB–1 GB; cap retention |

Reserve memory and disk for the operating system, filesystem caches, updates and image rollouts. The current core service reservations total roughly 6 GB before adding a database, publisher and monitoring. That is why 12 GB is a better full-feature starting point than the previous 8 GB minimum.

Run the daemon as one writer. Its session/state databases and files have not become safe for multiple active daemon replicas merely because accounts uses PostgreSQL. Preserve tenant directory ownership, encryption and the installed-bundle ledger. A host restart must not erase state.

Keep identity and payments inside accounts: `identity/` and `payments/` are modules, not independent deployed services. PostgreSQL can contain separate schemas and roles for accounts, publishing and worker admission without requiring three database servers. Workers performing untrusted jobs receive no database credentials.

## Worker server services

The worker is a separate host because customer code must not execute beside all tenant data and signing credentials.

| Resource | Required behavior |
| --- | --- |
| Trusted controller | Accept authenticated jobs over WireGuard/private routing; launch and monitor isolated containers; never expose Docker control publicly |
| Executor jobs | Plugin enumeration and execution, foreground shell commands, workspace upload/download, result delivery and model/fetch broker frames |
| Builder jobs | Node/npm/Vite image with baked skeleton dependencies; isolate npm scripts and user sources |
| Browser sessions | Chromium/Playwright dependencies from the existing runtime image; per-tenant sessions, profiles, cancellation and expiry |
| MCP sessions | Support both persistent stdio servers and HTTP connections; preserve the session protocol and tenant ownership |
| Installer jobs | NSIS toolchain for per-agent Windows installers; bounded execution with only the necessary package inputs |
| Internet egress proxy | Reuse the public-address validation policy; deny private networks, metadata endpoints and direct paths around the proxy |
| Admission and queue | Atomic job leases, global and per-tenant limits, expiry and retry behavior; use trusted PostgreSQL operations or another atomic store |
| Independent reaper | Terminate jobs after deadlines even if the controller crashes; remove abandoned workspace copies and release leases |

Proposed initial admission limit: two 2 GB jobs at once on the 8 GB worker, with the rest queued. Persistent browsers/MCP sessions count toward capacity. This is a sizing assumption, not a measured performance claim. The current AWS lease code permits 64 global and eight per-tenant jobs; that limit must change for one small worker. Increase worker size/count when queue wait, memory pressure or session demand warrants it.

Use a current Ubuntu/Debian kernel with Landlock available for the code paths that require it. Validate seccomp behavior, cgroup limits, CPU/memory/process limits, disk quotas and Chromium on the actual host.

The proposed VPS isolation runtime is gVisor `runsc` using its `systrap` platform. It runs inside a VM without depending on nested KVM. This is a new integration, not an existing agentd backend or an assertion that it is identical to AWS microVM isolation. [gVisor platforms](https://gvisor.dev/docs/user_guide/platforms/) and [compatibility guidance](https://gvisor.dev/docs/user_guide/compatibility/) explain the requirements. Browser, Node, Python and MCP acceptance checks must pass before using it for customer jobs. If hardware microVM isolation must remain unchanged, choose a KVM-capable host or retain AWS execution and reprice that alternative.

Each job gets its own sandbox, temporary files and narrow object grants. No platform provider keys, publisher keys, other tenants' files, privileged mode, Docker socket or unrestricted network are available in a customer job. Trusted control services may hold scoped credentials; the launched jobs may not inherit them.

## Data and object storage

| Store | Location | Contents and access |
| --- | --- | --- |
| Accounts database | Main server PostgreSQL | Identity links, encrypted identity signing keys, sessions, users/orgs, balances, ledger, payment intents and GPU rental records |
| Publish database | Separate PostgreSQL schema/role | Creator roster, bundle ownership, encrypted signing material, pending metadata and atomic index locks |
| Worker control database | Separate PostgreSQL schema/role | Admission leases, job status, ownership, deadlines and cancellation; controller access only |
| Agentd persistent volume | Main server SSD | Agents, configuration, tenant workspaces, sessions, memory, installed-bundle ledger and encrypted credential records |
| `agentd-prod-registry` | Public R2 bucket with custom domain | Verified public index, approved bundles, engine installers and per-agent installers; publisher writes, clients read |
| `agentd-prod-pending` | Private R2 bucket | Presigned uploads, submissions awaiting approval and rejected/parked packages; no public domain |
| `agentd-prod-scratch` | Private R2 bucket | Builder sources/results/logs, executor workspace copies and broker/session messages; short expiry and narrow grants |
| `agentd-prod-backups` | Private R2 bucket | Encrypted PostgreSQL backups/WAL, consistent daemon-state backups, publisher records and deployment configuration |
| Worker temporary disk | Worker SSD | Disposable job directories and bounded image/cache storage; no shared mount of the main tenant volume |

Use separate R2 credentials and access policies for publishing, scratch transport and backups. R2 public access applies at the bucket level, so private pending uploads cannot simply be put under a prefix of a public registry bucket as in the existing S3 policy. Splitting buckets requires corresponding application wiring.

R2 is S3 compatible, but the existing code explicitly constructs AWS S3 endpoints and URLs in several places. Add endpoint, addressing style and public-base configuration to builders, executor, publisher and release scripts. Validate presigned PUT/GET, CORS, multipart uploads, object existence checks and expiry. See [Cloudflare's boto3 configuration](https://developers.cloudflare.com/r2/examples/aws/boto3/).

At [current R2 Standard prices](https://developers.cloudflare.com/r2/pricing/), the first 10 GB/month are free and additional storage is US$0.015/GB-month: 100 GB retained costs US$1.35 and 500 GB costs US$7.35. Included operations are one million writes and ten million reads monthly; broker polling can exceed these. Egress is free. The 100 GB assumption means total object storage across all buckets, not 100 GB in each. It does not include the VPS disks.

Keep provider snapshots plus independent encrypted backups. Use PostgreSQL backup tooling that supports recovery from a base backup and WAL, consistent backups of daemon SQLite/state files, explicit retention, and restore drills. Keep encryption recovery keys outside the backed-up data and outside customer-job hosts. A snapshot alone does not prove the ledger or SQLite files can be recovered consistently.

## Domains and public routes

Preserve the product's existing domain names where practical:

| Host or route | Destination |
| --- | --- |
| `platform.nakamaai.co` | Web client plus platform discovery |
| `admin.platform.nakamaai.co` | Admin UI; accounts still enforces admin authorization |
| `builder.platform.nakamaai.co` | Cloud Agent Builder via daemon host mapping |
| `comfypenguin.com` | Comfy Penguin via daemon host mapping |
| `*.platform.nakamaai.co` | Published agents via the existing wildcard-host behavior |
| Registry custom domain, for example `registry.nakamaai.co` | Public R2 registry bucket |
| Accounts API, model proxy and ingest | Separate HTTPS hostnames or explicit path routing; configure discovery and clients together |
| Publish API | Authenticated public publishing routes; not the worker controller |
| Agent webhook ingress, when enabled | Signed/authorized routes to the daemon webhook listener, currently default port 8788 |

Use internal container DNS for sibling services, and WireGuard or equivalent restricted transport between main and worker. PostgreSQL and worker control are not public APIs. Only the reverse proxy exposes application services; SSH is restricted to operators.

Prefer public HTTPS/WSS on port 443 for the final deployment. Existing clients use ports 4000, 4100, 4200, 4300 and 8787. Cloudflare's standard proxy does not handle these ports: keep compatibility listeners on DNS-only records during transition, or rebuild all clients onto the new routes before turning on proxying. [Supported ports](https://developers.cloudflare.com/fundamentals/reference/network-ports/).

Automate wildcard certificate issuance with a scoped DNS token and DNS-01. [Let's Encrypt wildcard validation](https://letsencrypt.org/docs/challenge-types/). Keep the public issuer stable across accounts and model proxy; configure JWKS, CORS, cookies, OAuth callback URLs, allowed return origins and WebSocket forwarded headers as one change. A new issuer can require users to sign in again.

The desktop core flavor still embeds staging registry, publish, accounts, proxy and ingest addresses. Update `clients/desktop/flavors/`, web build arguments, marketplace links and engine/product installer configuration. Build and distribute new installers, not just the server containers. Pin the existing publisher trust anchor or perform an explicit coordinated key rotation; otherwise existing clients reject packages.

## External accounts and credentials

| Account or credential | What needs configuring |
| --- | --- |
| OVH | Billing account and two servers in one region; operator access and backups |
| Cloudflare | Existing apex-domain zones, R2 subscription, four buckets, public registry custom domain, scoped storage and certificate/DNS tokens |
| GitHub | Existing repo, GHCR permissions, build/deploy workflow credentials, release assets and retention |
| Google login | Production OIDC client ID/secret and exact callback/return origins; web login is Google-only by the production setting |
| Google integrations | Separate `GOOGLE_OAUTH_CLIENT_ID`/secret and user consent configuration for agent integrations; these are distinct from platform login |
| Model providers | Funded credentials for every enabled model route: OpenAI, Gemini, DeepSeek and Moonshot as applicable. Current default text/vision configuration uses OpenAI; remap unsupported/unfunded routes deliberately |
| Dodo Payments | Live API host/key, webhook verification secret, production product ID and return URLs; Dodo is the configured production rail |
| Vast.ai | Funded platform key, approved rental image, GPU/disk/price/concurrency limits, credit charging and cleanup schedules |
| Comfy partner models | Platform `COMFY_API_KEY` and credit budget for paid partner nodes |
| Model downloads | `HF_TOKEN` and `CIVITAI_TOKEN` when gated models require them |
| Search | Configure the current Gemini/Parallel/DuckDuckGo chain; Brave key only when enabled |
| Optional agent integrations | Per-user OAuth or tool credentials and installed MCP dependencies for the enabled agent; platform hosting alone does not supply access to private external services |
| Alerts | A working destination plus an independent check that detects main-host failure and missed cleanup jobs |

Stripe and Razorpay adapters also exist but are alternatives to Dodo; all three do not need separate purchases. Payments and GPU rentals are already metered through accounts, so preserve those books and webhook idempotency.

Store `ACCOUNTS_INTERNAL_KEY`, `LITELLM_MASTER_KEY`, gateway/admin credentials, worker internal keys, `AGENTD_IDENTITY_KEK`, `AGENTD_STATE_KEK`, publisher encryption keys and backup recovery keys as separately scoped secrets. Do not generate replacements for encryption keys before determining whether existing encrypted records must remain readable. Preserve keys with their data during migration. Rotating a wrapping key is not the same as rotating a public JWT signing key.

Accounts already supports environment-provided secrets with `AGENTD_APP_SECRET_ID=local`; its production PostgreSQL requirement must remain enabled. However, the admin console's secret/creator-key panels and key-change service rolls currently call AWS Secrets Manager, DynamoDB/KMS and ECS. Those controls require new storage/rotation/restart adapters for a complete migration.

## Scheduled work and monitoring

Use systemd timers or a small scheduler with authenticated internal calls, retries, UTC timing, execution records and missed-run alerts.

| Work | Required cadence or policy |
| --- | --- |
| Subscription renewals | Hourly at minute 00: `/subscriptions/renew-due` |
| Expired credit accounting | Daily 00:05 UTC: `/ledger/close-expired` |
| Ledger snapshot | Daily 00:20 UTC: `/ledger/snapshot` |
| Vast cleanup and GPU charging sweep | Every minute: `/vast/reap`; check `/vast/reaper` liveness |
| Sandbox reaper | Independently sweep expired/orphaned jobs, including persistent sessions; every minute is the starting policy |
| Backups | Scheduled database/state backups and continuous WAL as configured; retention/pruning and failure alerts |
| Certificates | Automatic renewal with failure alerts |
| Host and data maintenance | Image/log/cache cleanup, disk-space monitoring, patching and retained rollback versions |

Keep the daemon's own per-user cron/channel state on its persistent volume. These are separate from platform billing and infrastructure cleanup schedules.

Container stdout currently carries JSON logs and CloudWatch EMF metrics. Moving that output to local files preserves logs but does not recreate CloudWatch graphs or alarms. Add EMF extraction into a local metrics system, or replace the telemetry output with an exporter. Carry forward the existing checks for unbilled spend, ledger failures/backlog, overspend, proxy failures, accounts health, login failures, latency, job liveness and GPU reaper liveness. Cap label cardinality and retention.

## Remaining AWS data on October 12

Read-only inventory in AWS account ending 8944 found:

- Mumbai has no ECS clusters, EFS filesystems, DynamoDB tables or app secrets. The old production publish KMS key is **PendingDeletion for October 17, 2026 at 12:10:36 JST**. Determine whether any retained ciphertext or signing backups rely on it before that deadline. Its alias alone does not contain the private signing key. No cancellation or other cloud change was performed.
- Tokyo retains an `agentd-staging` ECS cluster, but it has no services, EC2 instances or load balancer. Two scheduler/watchdog Lambdas remain.
- Tokyo retains the staging app secret, enabled staging publish KMS key, creator/ownership/lock DynamoDB tables, and an EFS filesystem reporting only 6,144 bytes.
- Three staging S3 buckets remain: registry, marketplace and builder scratch. Each currently reports zero objects. They cannot supply the old bundles or engine installer without a separate backup/build.
- The staging app secret has a configured external Neon database URL. Database contents and recovery availability require a separate database check before migration. Do not treat a database URL as proof of a recoverable production ledger.
- Required model, payment, Google client-secret, Vast, Comfy and gated-download credentials in that remaining secret are missing/placeholders. Reconfigure them from the relevant provider accounts; booting servers does not make these features usable.
- Route 53 still contains old apex/staging zones and a private `agentd.local` zone. Preserve required records before replacing DNS or deleting remnants.

The older September US$195.33 pretax bill included staging and the earlier deployment. US$30–40 would be US$155–165 less than that historical bill, about 80–85%. It is not a saving against the now-stopped compute fleet, and it does not establish a production-only migration saving.

## Implementation needed before deployment

1. Create production manifests and host provisioning for both servers. The current Compose file has only model proxy, accounts, daemon and web, dev credentials and localhost origins; it lacks PostgreSQL, ingest, publishing, production schedules and isolated worker control.
2. Implement the remote controller and isolation backend. Preserve plugin jobs, builds, browser/MCP sessions, streaming/broker frames, workspace changes, cancellation, leases, deadlines, persistent-session cleanup and bounded queues. Do not substitute the single-process development HTTP runner: it lacks production session/control parity.
3. Add configurable S3-compatible transport and split public registry from private submissions. Remove hard-coded AWS regional URLs and supply new public index/installer addresses.
4. Implement PostgreSQL creator/owner records and atomic publish locks, plus encrypted signing-vault storage and a production HTTP entry point. The publish image currently starts the AWS Lambda runtime and the composition root directly selects DynamoDB/S3/KMS adapters.
5. Replace admin AWS infrastructure controls with scoped local/remote controls that update secrets and restart only their consumers. Keep creator approval, unlisting, key visibility and rotation working.
6. Recreate billing and cleanup schedules, telemetry extraction, dashboards and alert delivery. Reap GPUs and worker jobs independently from request handling.
7. Import recoverable database, tenant state, signing records and keys. Rebuild/publish registry content and engine installers where buckets are empty. Preserve trust and money records; this is not a fresh database reset.
8. Configure provider credentials, Google consent/callbacks, live Dodo checkout/webhooks and funded Vast/Comfy accounts. Enable the intended hosted agents and integrations through their declared configuration.
9. Rewrite AWS-specific GitHub deployment paths to build/push GHCR images and deploy pinned versions onto the new hosts. Rebuild desktop/web/marketplace releases against production URLs. Keep CI runners separate from customer worker jobs.
10. Cut DNS over after acceptance checks. Remove staging/AWS remnants only after the database, signing and client compatibility requirements have been resolved.

## Acceptance checks

Deployment is complete when these user paths and recovery checks work on the actual hosts:

- Fresh browser and desktop sign-in, refresh/logout, Google callback, JWKS verification, cookies and CORS; admin access remains restricted.
- Model text/vision calls stream successfully, insufficient credits are refused, and usage lands exactly once in the account ledger.
- Real payment checkout returns correctly, signed webhooks grant credits once, replay does not duplicate credits, and renewal/expiry schedules run.
- Cloud Agent Builder creates and edits an app, npm/Vite builds run in isolation, tool enumeration/execution works, files synchronize and cancel/deadline cleanup terminates jobs.
- Browser login/profile handling and persistent MCP sessions work per tenant; another tenant's files, credentials and private network endpoints remain inaccessible.
- Creator admission/revocation, pending review, publishing, index signature verification, unlisting and Windows installer download/install all work.
- Comfy Penguin can rent a GPU, configure the expected ComfyUI endpoints, install gated models when authorized, render/download outputs, debit credits and destroy idle/orphaned rentals.
- Product and wildcard hostnames serve the correct agents; desktop installers and marketplace links point to production.
- Service/host restart retains data; a backup restores into a separate temporary environment; stalled billing/cleanup and host failure trigger an independent alert.
- The planned concurrent jobs and sessions stay within the memory/disk budget; overload queues or refuses work without killing accounts, model proxy or daemon.

## Capacity and total cost

US$30–40 is the hosting budget for the proposed small deployment, subject to stock and compatibility checks. Add:

`total bill = hosting + model/search/media usage + GPU rental/storage/network usage + payment fees + domain renewals + CI overages + any retained managed database or AWS services + tax`

The Vast code defaults to a US$1/hour offer ceiling and ten live rentals, which is a US$10/hour GPU compute exposure if all ten are active. That is a code policy, not an actual market quote. Start with a deliberate smaller cap and credit requirements. Twenty GPU-hours at an assumed US$0.50/hour would add US$10 compute; provider disk/network and partner-model charges are additional. Idle timeout is currently ten minutes and the reaper is scheduled every minute.

A single main host with a recoverable backup provides all listed features at small scale; it does not provide uninterrupted service during host failure. Automatic failover requires additional application/database resources and a change to daemon state ownership. More concurrent browsers/builds require more worker capacity. Do not quote a universal user limit from RAM alone.

## Code references

- Core service definitions and reservations: [variables.tf](../infra/modules/variables.tf), [services.tf](../infra/modules/services.tf).
- Production domain, payment and deployment defaults: [production/main.tf](../infra/environments/production/main.tf).
- Current four-service local setup: [compose.yaml](docker/compose.yaml).
- Worker dispatch/session/admission: [executor handler](../services/executor/handler.py), [Fargate dispatcher](../services/executor/fargate_dispatch.py), [session runner](../services/executor/session_runner.py), [leases](../services/executor/runtime_leases.py), [egress proxy](../services/executor/egress_proxy.py).
- Build isolation: [builder handler](../services/builder/handler.py).
- Publishing/signing: [publish handler](../services/publish/handler.py), [publish infrastructure](../infra/modules/publish.tf).
- Admin infrastructure controls: [admin_api.py](../accounts/admin_api.py); environment-secret startup: [accounts/app.py](../accounts/app.py).
- State encryption: [secret_storage.py](../agent_runtime/infrastructure/secret_storage.py).
- GPU rental policy and cleanup: [vast_factory.py](../vast/main/vast_factory.py), [instance_settings.py](../vast/application/instance_settings.py), [scheduled jobs](../infra/modules/variables.tf).
- Existing desktop service URLs: [core/distribution.toml](../clients/desktop/flavors/core/distribution.toml).
- Deployment and release workflows: [deploy.yml](../../.github/workflows/deploy.yml), [release-build.yml](../../.github/workflows/release-build.yml).
- Provider image storage billing: [GitHub Container Registry](https://docs.github.com/en/billing/concepts/product-billing/github-packages); [Actions allowances](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
