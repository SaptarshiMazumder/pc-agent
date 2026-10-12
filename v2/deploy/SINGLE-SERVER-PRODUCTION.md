# Single production server

Use [the implemented OVH deployment](ovh/README.md): one 12 GB Linux OVH server,
no staging, no GPU, **existing Neon database retained**.

The server runs agentd, accounts/identity/payments, the model proxy, web/admin UI,
marketplace, telemetry ingest, publishing and isolated build/tool/browser/MCP jobs.
Cloudflare supplies DNS and four R2 buckets; GitHub supplies images and releases.

The earlier hosting estimate was approximately **US$15.85/month** for the OVH
server plus 100 GB of R2 storage. The complete bill is:

`hosting + existing Neon bill + domain renewals + applicable tax + payment fees + storage/request/CI overages`

Model/API usage is excluded as requested. No GPU is provisioned. The Neon invoice
was not supplied, so US$15.85 is a hosting estimate, not the total invoice. Verify
OVH region, stock, current pricing and tax when ordering; this code does not order
anything or start charges.

Keep Neon, existing account/payment data, Google OAuth, Dodo, provider accounts,
domain registrations and GitHub. Replace AWS application compute, load balancing,
EFS, publishing DynamoDB/Lambda, S3, ECR and infrastructure schedules/monitoring
with the implementations described in the OVH runbook. Retire AWS only after
cutover and recovery checks, preserving signing identities and encryption keys.

The smallest configuration admits one active sandbox job/session; busy work retries.
Jobs use gVisor on the same physical host, not AWS hardware microVMs. Backup and
redeployment introduce maintenance interruptions; there is no automatic failover.
Actual Linux/runsc, Chromium, MCP and workload acceptance must pass before launch.
The old self-hosted PostgreSQL proposal is superseded: there is no database migration.
