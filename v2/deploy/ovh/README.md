# One production OVH host, existing Neon retained

This directory implements the agreed production topology. It does not purchase a
server, change DNS, touch the Neon database or remove AWS resources. Keep the same
Neon URL, identity encryption passphrase, daemon state encryption passphrase,
Google clients, Dodo account and publisher trust anchor.

## Resources to provision

| Where | Resource | Purpose |
| --- | --- | --- |
| OVH | One Linux amd64 VPS, starting with 12 GB RAM, 4+ vCPU and enough SSD for images/state | All application containers and one active isolated job |
| OVH host | Docker Engine + Compose plugin, gVisor `runsc`, Linux kernel with Landlock, iptables, Python 3, restic | Execution isolation and operations |
| Neon | Existing production project/database and existing connection string | Accounts, identity, payments, balances, organizations and ledger; no database move |
| Cloudflare | Existing DNS zones delegated there, scoped DNS edit token for Caddy | Domains, wildcard certificates and HTTPS |
| Cloudflare R2 | Public registry bucket with custom domain `registry.nakamaai.co` | Signed bundles, engine installers, catalog and roster |
| Cloudflare R2 | Private submissions bucket | Upload slots and parked packages awaiting review |
| Cloudflare R2 | Private scratch bucket | Build sources/results, per-job object capabilities and RPC |
| Cloudflare R2 | Private backups bucket | Encrypted restic recovery snapshots |
| GitHub | Existing repo, GHCR packages and existing Releases | Production images and desktop releases |
| Existing providers | Google OAuth, Dodo and model/search API accounts | Retained integrations; provider usage bills remain separate |
| External monitoring | Independent HTTPS monitor for platform/discovery and alert contact | Detect entire-host failures; local timers cannot do that |

Use distinct R2 credentials: publisher restricted to registry/submissions,
controllers restricted to scratch, and backup credentials restricted to backups.
Only the registry bucket is public. R2 upload PUTs are consumed once by their
authenticated owner; oversized objects are refused before reading into memory.
PUT URLs cannot enforce a POST upload size policy, so configure lifecycle cleanup
and storage/budget alerts too. Cloudflare documents the [supported S3 API](https://developers.cloudflare.com/r2/api/s3/api/).

Scratch lifecycle: expire `executor/jobs/`, `builder/sources/` and
`builder/results/` after one day; code cache after seven days. Submissions:
expire `pending/uploads/` after one day, **not** `pending/<creator>/` packages
waiting for review. Registry: no blanket expiry. Backups: let restic manage
retention, not object lifecycle expiration. Enable public GET CORS for registry
and signed GET/PUT/HEAD CORS for scratch/submissions from the production app
origins (include `Content-Type`; no cookie credentials). Test desktop uploads too.

## Services and isolation

Compose runs Caddy, web/admin UI, marketplace, accounts, LiteLLM model proxy,
agentd, ingest, publishing, builder/executor controllers, the private runtime
controller, an egress proxy and one billing scheduler. There is **no PostgreSQL
container**, staging service or GPU allocation. Vast modules remain installed
because accounts imports them; do not add a Vast API key or its rental/reaper schedules.

Each build/tool/browser/MCP job gets its own gVisor container and internal network,
public-only egress proxy, read-only root, temporary workspace, 2 GB RAM ceiling,
one CPU and 256 PIDs. It receives exact-object signed URLs and caller-owned job
settings, with no Neon, publisher, provider, controller or host credentials/mounts.
Only the trusted controller has Docker socket access. The `adjob+` host firewall
rule is mandatory: Docker internal networking alone does not deny host gateways.
There is no fallback to local execution when isolation is unavailable.

One active job/session shares the worker slot across builds and tools. Busy calls
are refused with a retryable busy response; there is no durable job queue.
Browser/MCP sessions have a 25-minute maximum and five-minute idle deadline.
The independent host timer also reaps abandoned jobs when the controller dies.
The daemon's existing `microvm` setting selects the remote executor **protocol**;
on this deployment its execution boundary is gVisor, not an AWS hardware microVM.
All workloads share one physical host, with downtime on host failure and deployment.

## Prepare the host

Install Ubuntu 24.04 or equivalent amd64 Linux with a compatible kernel. Install
Docker/Compose from [Docker's official instructions](https://docs.docker.com/engine/install/ubuntu/).
Install gVisor from its signed release apt repository using the current
[gVisor installation instructions](https://gvisor.dev/docs/user_guide/install/)
and confirm `docker run --rm --runtime=runsc hello-world`. The default systrap
platform avoids a KVM requirement; validate it on the actual OVH host.

Install `python3-venv`, `iptables`, `restic`, `curl` and `util-linux`. Clone this
feature branch to `/opt/agentd/repo`. Create a separate operator Python environment
with `cryptography` and `boto3` for configuration/import tools. Restrict SSH to your
operator IP/key. Allow inbound 80, 443/TCP+UDP, 4000, 4100, 4200, 4300 and 8787 only
for the application; never expose 4400/4500/4600 or Docker. Ports beyond 443 keep
existing client contracts, so the platform DNS record must be **DNS-only** while
these URLs are used. Caddy renews wildcard certificates with DNS-01.

In `v2/deploy/ovh`, copy `.env.example` to `.env` and set public deployment values.
Fill an operator JSON file **outside the repo** using `operator-input.example.json`.
Use the exact existing production `DATABASE_URL`, `AGENTD_IDENTITY_KEK` and
`AGENTD_STATE_KEK`; generating replacements would strand encrypted data. No
credential values are needed in the public `.env`. Do not run `docker compose
config` on secret-filled environment variables or paste secret files into chat.

```bash
cd /opt/agentd/repo/v2/deploy/ovh
/opt/agentd/operator-venv/bin/python configure.py --input /root/agentd-operator.json
```

Configuration generates new inter-service keys and the new publisher wrapping key,
then writes private service-specific files. It refuses to overwrite an existing
secret directory. Accounts admin can update configured model/OAuth/payment keys
through the private control API and restart their consumers; encryption keys and
Neon connection changes require an explicit operator migration. Keep the generated
publisher KEK and restic password in an independent recovery vault.

## Preserve data and signing identity before startup

Restore the old daemon's full persistent tree into `/opt/agentd/state/daemon`,
including tenant layouts, configs, state databases and encrypted secrets; retain
UID/GID 1000. Keep the database in Neon, with the same identity KEK. Preserve any
existing issuer setting if moving an already-live production identity; the sample
uses `https://platform.nakamaai.co:4100`. Old staging tokens remain a different issuer.

Copy public registry objects to R2 with the **same keys and checksums**. Split old
`pending/` objects into the private submissions bucket, never the public bucket.
Copy retained installers/engine releases too. Relative index URLs migrate directly;
review absolute legacy URLs and installer payloads for embedded old endpoints.
Bundle/installer signatures cover digests: changing a payload means rebuilding,
re-signing with its existing creator identity and publishing a new version.
Refresh catalog links/web host without altering the signed roster or root.

Import the original offline root key and, if present, existing creator/owner tables.
Do **not** run keygen or replace the client trust anchor. The importer verifies
each pair, rewraps private keys in memory, preserves creator IDs and bundle owners,
and refuses identity/ownership changes. It reads AWS tables and never mutates them.

```bash
export PYTHONPATH=/opt/agentd/repo/v2
/opt/agentd/operator-venv/bin/python import_registry.py \
  --secrets-file /opt/agentd/state/secrets/publish/env.json \
  --database /opt/agentd/state/publish/registry.db \
  --root-key /root/original-root.json \
  --expected-root 'Fkez25JIQDUtvwvyghwGdY2Mw//fDG7ITZCmB7CG7Nc='
# Add --aws-creators TABLE --aws-owners TABLE --aws-region REGION for existing rows.
# Use an operator AWS profile with read-only table access and KMS decrypt authority.
chown -R 1000:1000 /opt/agentd/state/publish
```

If a source KMS key is pending deletion, recover/decrypt the original data before
its deletion deadline. Do not rotate or destroy it during this migration. The
production preflight refuses startup until the imported root matches client trust.

## Build and start

Once the workflow is available on the repository's default branch, run manual
**OVH production images** for the chosen feature branch/commit. It pushes
amd64 images tagged with the commit SHA to GHCR; it never deploys or touches AWS.
Set repository variable `AGENTD_HOSTING=ovh` at cutover; legacy AWS deployment and
registry workflows then skip their AWS entry jobs. This does not disable the
live AWS watchdog or billing schedules, which must be retired separately.
Set `IMAGE_PREFIX=ghcr.io/saptarshimazumder/pc-agent`, `IMAGE_TAG=<that SHA>` in `.env`.
The host needs a read-packages GitHub token if packages are private. Existing desktop
release tooling remains; the core distribution now points at production/R2.
Use `node ../../clients/desktop/scripts/sync-platform-urls.mjs --ovh-env .env --strict`
from this directory to stamp all existing cloud flavors from OVH public config
before creating desktop releases. `--check` reports drift without rewriting.

```bash
bash firewall.sh
# Stop the old AWS billing schedules first; only one clock may target this Neon DB.
bash deploy.sh --pull
# --build is available for local host builds instead of GHCR, daemon first.
bash health.sh
docker compose exec -T executor python - < host_smoke.py
source /opt/agentd/state/secrets/backup.env
restic init  # Once, only for an empty backup repository.
bash install-timers.sh
bash backup.sh
restic check
```

The stateful services remain single writers. Billing runs renewals hourly and
close-expired/snapshot daily at 00:05/00:20 UTC; successful time slots persist and
failures retry. No duplicate AWS clock may run against the same Neon database.
Logs/EMF stay in bounded Docker local logs; use `docker compose logs`. Health and
reaper timers log to journald. Configure your external monitor to alert an operator.

Backups at 00:30 UTC pause accounts, publishing, scheduler and daemon for a
consistent restic snapshot. This introduces an off-peak maintenance interruption.
The Neon database continues using its existing provider recovery settings. The
backup archive includes host state and encrypted service files, not a Neon dump.
On a replacement host restore restic into a separate directory, restore the state
and secrets with original ownership, point accounts at the same Neon database,
reinstall the firewall/runtime/timers and run acceptance checks. Test a restore
before deleting source AWS data. Keep a local checkout of manifests and recovery
credentials in case both this host and a provider account become unavailable.

## Acceptance and cutover

These checks must pass on the Linux host before public DNS or AWS teardown:

1. Every private endpoint passes `health.sh`; discovery advertises the real public
   HTTPS/WSS addresses and login works from web, desktop and app vanity domains.
2. Existing Neon accounts, balances, devices and organizations are unchanged;
   Google login/refresh/logout and Dodo signed webhook idempotency work. Validate
   payment behavior using the provider's test environment without charging users.
3. A model call is authenticated/metered into the existing ledger. Verify external
   API calls, search and caller OAuth credentials reach their intended providers.
4. `host_smoke.py` passes shell, Chromium, build and network isolation on the actual
   runsc host; a sample untrusted tool and MCP session work through agentd as well.
   Cross-owner cancellation, missing runsc, exhausted capacity, deadline cleanup,
   controller crash and host reboot must all remain fail-closed.
5. Build/edit an agent UI, run its tools, publish it, admit its creator, verify bundle
   and roster signatures and install its Windows installer on a clean machine.
   Registry downloads and marketplace links must use the new destinations.
6. Exercise backup restore, billing-clock retry, certificate renewal and external
   outage alerting. Verify memory headroom with the daemon and one browser/build
   job active; increase host capacity if real load exceeds this starting budget.

DNS targets: platform, admin, builder, wildcard platform, Comfy vanity and store
to the OVH address; registry to its R2 custom-domain target. Preserve unrelated
MX/TXT records, Google callback URLs and Dodo webhook paths. Optional daemon webhook
listener 8788 is not enabled by this manifest; add an authenticated Caddy route if
an existing deployment uses it, before disabling the corresponding old listener.

## Replace AWS after acceptance

Disable AWS deployment automation, watchdog Lambda and EC2/ECS autoscaling first.
Stop the old billing schedules before enabling the OVH scheduler. After DNS
cutover, monitoring and restore checks, retire EC2/ECS/Fargate, ALB/target groups,
execution/build/publish Lambda, scheduler relay/watchdog and AWS schedules; replace
EFS with host state, Dynamo publishing tables with encrypted local metadata,
S3 with the four R2 buckets, ECR with GHCR, CloudFront site with the host marketplace,
and CloudWatch infrastructure logs/alarms with bounded local logs plus external
monitoring. Retire unused VPC endpoints, IPs and networking only after verifying
they serve no other project. Preserve source signing data and backups through the
rollback window. Review leftovers in both Tokyo/staging and Mumbai/production.

Keep **Neon**, domain registrations, Google OAuth, Dodo, model/search integrations
and GitHub. Keep source AWS signing keys/metadata and DNS authority until recovery
and delegation are verified; then retire remaining AWS secrets/KMS/Route53 resources
according to their actual shared usage. This directory deliberately has no
automatic AWS deletion command.
