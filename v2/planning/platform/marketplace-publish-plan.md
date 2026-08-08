# Publish to the cloud marketplace — plan

**Goal (user's words, 2026-08-08):** an agent built by Agent Builder is published to the AWS
cloud, and from there can be downloaded and used **in cloud mode** — both the desktop client
signed into the platform and the AWS web client.

**Status:** not started. This document is the design; nothing below is built.

---

## What already exists (do not rebuild any of it)

The surprise, on auditing: everything DOWNSTREAM of publishing works. The registry is not a
stub — it is a signed, multi-creator, per-account store missing exactly one thing.

| piece | where | state |
|---|---|---|
| registry bucket, public read, HTTPS | `infra/modules/registry.tf` | built |
| hosted daemon points at it | `services.tf` — `AGENTD_REGISTRY` + `AGENTD_PUBLISHER_KEY` | built |
| index format, sha256 + ed25519, fail-closed | `marketplace/registry_client.py` | built |
| **schema-2 multi-creator trust** | `marketplace/trust.py`, `roster_builder.py` | built |
| replay protection (newest roster remembered) | `trust.RosterMemory` | built |
| **per-account install** on the hosted daemon | `gateway._marketplace_for()` — scoped config | built |
| pack / index / sign / upload | `cli/commands/bundle.py`, `publish-registry.yml` | built |
| Store UI (web + desktop) | `clients/ui` `AppView.tsx` | built |
| `package_agent` produces the `.agentpkg` | agent-builder's own plugin | built |

### The trust model is already the right one

Schema 2, and the split matters for everything below:

```
platform ROOT key  --signs-->  the ROSTER (who is a creator, and their public key)
creator's own key  --signs-->  that creator's bundles
client             --pins-->   the ROOT key only  (distribution.publisher_key)
```

Adding a creator never re-pins an installed client. And, quoting `roster_builder.py`:

> That split is what makes a publish API safe to run as a service: the service verifies a
> submission against the submitter's ALREADY-listed key, and never needs the root private key.

So the publish service is an ordinary web service. The root key stays offline.

## What is missing

1. **No intake.** No `POST /publish` anywhere; the gateway's marketplace methods are
   `catalog / installed / install / uninstall / progress`. A `.agentpkg` on a user's disk has
   no route to the bucket.
2. **No creator identity.** `--publisher-id` exists as a flag; nothing maps an *account* to a
   creator id, and nothing issues a creator a key.
3. **No way for a browser-only creator to sign.** The whole signing story assumes a CLI on a
   desktop. Someone who built their agent in the hosted web client has no keypair and no shell.
4. **`agentd bundle publish` rewrites the whole index** from a repo checkout. That is a release
   job, not a per-user append, and two concurrent publishes would clobber each other.

---

## The one real decision: who holds a creator's private key?

**Option A — platform-held, per creator (RECOMMENDED).** On first publish the service mints an
ed25519 key for that account, keeps the private half in KMS, and signs on their behalf.

- Works in a browser, which is the requirement.
- Weaker in theory: we *could* forge a creator's bundle. We are already the root of trust and
  we serve the bucket, so this concedes nothing an attacker did not already get by owning us.
- Everything client-side is unchanged — a signature is a signature.

**Option B — bring your own key.** `agentd bundle keygen` locally, upload the public half.

- Genuinely stronger: a compromised platform cannot publish as you.
- Impossible for the target user in this request.

**Take A now, add B later as an opt-in for publishers who want it.** They coexist: the roster
holds a public key per creator and does not care where the private half lives.

## Roster re-signing is the approval gate — treat it as a feature

The root key is offline (a GitHub secret), so adding a creator to the roster cannot be automated
without putting that key online. That sounds like a blocker and is actually the review step a
marketplace needs anyway:

- **v1:** a creator's first publish files a *pending* record. An operator runs the roster
  workflow to admit them. Once listed, all their later publishes are self-service.
- **later:** batch admissions on a schedule if the queue justifies it.

Revocation already exists (`bundle roster` revoked list) — that is takedown.

---

## Phases

### P0 — creator identity

- `creator_id` derived from the account id (stable, opaque, not the email).
- A `creators` table: account -> creator_id, display name, KMS key id, state
  (`pending` | `listed` | `revoked`).
- `agentd bundle roster add` gains a mode that reads pending creators, so admitting a batch is
  one command and one signed artifact.

### P1 — the publish service

**Shape: a Lambda behind the existing ALB** (not a gateway RPC, not a new ECS service). It needs
S3 write and KMS sign and nothing else; it is stateless and idle most of the time. Putting it on
the agentd daemon would give the *runtime* container write access to the *store*, and app
connections must never be one bug away from publishing.

`POST /registry/publish` — multipart: the `.agentpkg` + the intended bundle id.

1. **Authenticate** against the accounts service (same token the daemon already accepts).
2. **Resolve the creator.** Not listed -> record as pending and return `202 awaiting review`
   with a message that says so plainly. Revoked -> `403`.
3. **Validate the package** — `read_manifest`, size cap, id is well-formed, `version` present
   and *newer* than any existing copy (installs supersede by version; a re-pack of the same
   number silently does nothing, which must be an error here rather than a mystery later).
4. **Own the id.** A bundle id belongs to the first creator who published it. Anyone else gets
   `409`. This is the whole squatting/hijack surface.
5. **Sign** the entry with that creator's KMS key.
6. **Append to the index under a lock.** S3 has no transactions, so a DynamoDB conditional write
   is the lock; the alternative is a single-writer queue. Read index -> append/replace this one
   entry -> write back. Never a full rebuild: this must not depend on a repo checkout, and it
   must not disturb another creator's entries.
7. Return the bundle url + version.

Reuses `index_builder`/`bundle_io` for the entry shape so the service and the CI job cannot
produce different index rows.

### P2 — `publish_agent`, an Agent Builder tool

Sits next to `package_agent` in `agents/agent-builder/plugins/agent-authoring/`:
validate -> pack -> POST -> report the outcome, including `202 pending review` as a normal,
explainable result rather than an error. Same rule as `package_agent`: refuses on validation
errors, so a broken agent cannot be published.

### P3 — nothing (the download path already works)

Once the entry is in `index.json`, both clients see it and install it into the caller's own
account. Worth an end-to-end test, not new code.

### P4 — the safety work this opens up

Publishing turns "code the user wrote" into "code strangers run". Each of these is a gate on
going public, not a nice-to-have:

- **`create_tool` is RCE on a shared container.** The `tool_workshop` flag was deleted because
  the agent boundary replaced it — sound on a desktop, not on a multi-tenant host where any
  signed-in account reaches Agent Builder. Either gate authoring tools off in hosted mode, or
  give each account its own runtime. **This blocks opening publishing to the public.**
- **Agent-private plugins are discovered from `registry.agents_dir` and
  `set_agent_tools()` replaces the map wholesale.** Suspected: one account's `reload_agent`
  drops another's agent tools. Needs a test before anything else here is trusted.
- **The sandbox must actually be on.** A published agent's private tools are untrusted on the
  installer's machine — which is what makes this safe — but the shipped backend is still the
  in-process passthrough unless `AGENTD_SANDBOX_BACKEND=subprocess`. Confirm the hosted task
  sets both, and that the subprocess backend is the hosted default.
- Per-creator publish quota and package size cap.
- Static scan of the bundle before listing (imports, entry points, size).

### Out of scope

Payments and entitlements. Per the earlier decision the seam stays a `NullPaymentProvider`;
this plan makes agents *distributable*, not *sellable*.

---

## Order

P0 -> P1 -> P2, then P4 before anyone outside the team can publish. P3 is a test.

The Terraform for the Lambda, its role, the DynamoDB lock table and the ALB rule will be
authored here and handed over as a command card — every `terraform` and `aws` command is the
user's to run.
