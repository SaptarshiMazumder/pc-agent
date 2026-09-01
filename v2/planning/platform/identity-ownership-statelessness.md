# Identity, ownership, and a stateless daemon — study + proposal

Status: BUILT (uncommitted). Steps 1–4 implemented and tested; Step 5's audit
done, its Postgres half deliberately gated on running more than one replica
(§5 Step 5 is the runbook). LGTM'd 2026-08-11.

The trigger: a signed-in user tried to publish `bedtime-kids` and got
`RuntimeError: no agent ... under .agentd/accounts/<acct>/installed/agents`.
That error is not a one-off bug. It is the system telling us that "whose agent
is this" has no answer anywhere in the code.

---

## 1. The mess, named

Five problems, one root cause.

1. **Ownership is guessed from folder location.** An agent in the overlay
   folder is "yours", one in the shared folder is "not yours". No record says
   who owns what. So publish, the sidebar, and Agent Builder each guess
   differently — and disagree.
2. **Two modes, branched everywhere.** `is_hosted`, `multi_tenant`, accounts
   on/off, overlay-or-not. Every feature has to get 4 combinations right.
   Publish got one wrong. The next feature will get a different one wrong.
   This is exactly the "if x then y, if z then a" style we want dead.
3. **Hosted daemon shows everyone everything.** The shared catalogue on EFS is
   visible to every signed-in stranger. There is no "my agents" view. People
   can step on each other's agents and workspaces.
4. **Login has had three designs.** The merged branch's design (session token
   per connection, contextvars, nothing persisted) is the right one, but
   leftovers of the old designs still exist (e.g. the `AGENTD_MODEL_PROXY_KEY`
   fallback chain in `http_publisher.py`, comments that claim the overlay is
   "None on every desktop install").
5. **Statelessness is a habit, not a guarantee.** Most durable state already
   lives outside the process (EFS, accounts DB, S3), but nothing enforces
   that, and nobody has written down what a daemon replica is allowed to keep
   in memory.

---

## 2. How big platforms handle this exact case

The GPT-store / app-store model, stripped to its bones:

- **Everything has an owner.** Every resource (agent, workspace, chat) carries
  an `owner` field in data. Questions like "can I publish this?" are answered
  by reading a field, never by checking which folder a file sits in.
- **One tenancy model, not two modes.** There is no "cloud version" and
  "desktop version" of the logic. Everyone is a tenant (an account with its
  own space). A desktop install is simply a deployment with ONE tenant — the
  machine's owner. The code path is identical; only two plug-in points differ:
  *who resolves the account* and *where the files live*.
- **A three-stage agent lifecycle.**
  - **Draft** — lives in the author's own space. Private. Editable. Publishable.
  - **Published** — a signed, versioned, immutable copy in the registry.
    Nobody edits it in place, ever. Fixing it means publishing a new version.
  - **Installed** — a *reference* in a user's space pointing at a published
    version ("bedtime-kids@1.0.1"), like a line in package.json. Not an
    editable copy.
- **Control plane vs data plane.** The control plane is the small set of
  services that *decide* things: accounts (who are you), registry (what
  exists, who owns it), billing. The data plane is the daemon: it *executes*,
  holds nothing durable, and any replica can serve any user.
- **Stateless workers.** A worker may die at any second. Everything durable is
  in shared storage; everything in worker memory is a rebuildable cache. Users
  reconnect and land on any replica.

This is Salesforce, Slack, ChatGPT, Shopify. None of them have an
`if single_user:` branch in business logic.

---

## 3. The one model for us

> **Everyone is a tenant. Desktop is a one-tenant deployment.**

Concretely:

- Every connection runs as an account. On a desktop install with no sign-in,
  the account is a built-in **local owner** account — resolved locally, no
  server call, no network. It is not "no account"; it is *the* account.
- The difference between desktop and cloud collapses into two adapters chosen
  once at boot (this is already the codebase's port/adapter style):
  - **AccountResolver**: hosted → ask the accounts service; desktop → return
    the local owner.
  - **StateRoot**: hosted → `/data/tenants/<acct>/`; desktop → `~/.agentd/`.
- After that there is *no* `if hosted` in agent listing, creation, publishing,
  or workspace code. One path. The branching we hate moves into configuration,
  where it belongs.

And ownership becomes data:

- Each agent directory gets a small metadata record (written by the runtime,
  not by authors — `agent.toml` stays the author's file):
  - `owner`: account id (or `platform` for curated agents)
  - `origin`: `authored` | `installed` | `curated`
  - `source`: registry id + version, when origin is `installed`
- `agents.list` returns `owner`/`origin` per row. Every UI renders from that
  data. "Can I publish X?" = `owner == me AND origin == authored`. One rule,
  no folder checks, no per-surface special cases.

What each user sees on hosted: **their drafts + their installs + the
platform's curated set.** Nothing else. The "shared folder everyone can see"
stops existing as a concept — curated agents are just the platform account's
published agents, installed by default.

### Our agents (main, agent-builder, anything we promote later)

They are not an exception to the model — they are the `curated` class:

- `owner = platform`, `origin = curated`. In everyone's list by default;
  editable and publishable by nobody but us (we ship new versions through the
  same registry pipeline every author uses).
- **The curated set is deployment data, not a list in code.** Making any
  agent "ours and available to all" = publish it under the platform account
  and add it to the deployment's curated seed. bedtime-kids could join
  tomorrow without touching a line of code; agent-builder could leave the
  same way.
- **main**: its *definition* is curated, but its *data* (chats, workspace,
  memory) is per-tenant — one shared brain definition, nobody shares a
  conversation. Already true today; the model just names it.
- **agent-builder** declares `requires_local = true` (it hot-loads Python).
  Offering it to all hosted users is the operator overriding that via
  `hosted_agents_allow` + the plugin sandbox — the lever exists; Step 3 is
  where the hosted deployment starts using it deliberately.

---

## 4. Stateless daemon — what it actually takes

The rule: **a daemon replica may die at any moment and nothing durable is
lost.** What that means for us, layer by layer:

| state | where it lives | on daemon death |
|---|---|---|
| identity + sessions + credits | accounts DB | untouched — next connection resolves the same token on any replica |
| agent definitions, workspaces, transcripts | EFS under the tenant root | untouched |
| marketplace artifacts | S3 | untouched |
| the in-flight turn (a running chat message) | daemon memory | dies; client reconnects and retries — acceptable |
| registry scan cache, tenant LRU | daemon memory | rebuilt on next boot — cache only, by definition |

Most of this is already true. What's missing is making it *guaranteed*:

- An **audit**: enumerate every write the daemon makes outside the tenant
  root / accounts DB / S3. Anything found is a bug against the rule.
- **Sessions already survive** — the token is a bearer credential resolved per
  connection, so there is no in-memory session store to lose. This was the
  merged login branch's best decision; keep it and delete the leftovers.
- The **one real scaling limit**: the accounts DB is SQLite on EFS —
  single-writer. Fine at today's scale. The step to N daemon replicas requires
  moving it to managed Postgres (RDS). That is a contained, later step; the
  daemon already talks to accounts over HTTP, so nothing in the daemon
  changes.
- Two replicas writing one tenant's files concurrently is a later problem
  (route a tenant's connections to one replica, or lease). Not needed until
  we actually run N>1.

---

## 5. Step-by-step plan

Each step ships alone and leaves the system better. Order matters: data model
first, then visibility, then identity cleanup, then scale.

### Step 1 — stop the bleeding (small, now) — DONE (uncommitted)
- `FileAgentRegistry.resolve_dir(agent_id)`: find an agent in the same
  shared+overlay union that discovery reads.
- `agents.list` rows gain `mine` (derived, for now, from "is it in my write
  layer" — desktop signed-out: everything is mine, correct).
- `publish_agent` / `package_agent` use `resolve_dir` + refuse to publish
  what isn't yours, with a reason ("part of this deployment, not your agent").
- Agent Builder greys out Publish/Edit on `mine: false`.
- Fixes today's RuntimeError and closes the hole where a hosted stranger could
  publish curated agents under their own name (today only prevented by
  accident — the lookup fails).

### Step 2 — ownership as data — DONE (uncommitted)
- Runtime-written metadata per agent: `owner`, `origin`, `source`
  (`.agentd-meta.json` beside `agent.toml`; never author-edited).
  Rules in `domain/ownership.py`, IO in `infrastructure/agents/ownership_store.py`.
- `registry.create` stamps `authored`+owner; every install path (store, CLI,
  web-app sync — they converge in `FileBundleInstaller.install_files`) stamps
  `installed`+source, which becomes `curated` when the platform itself installs
  (one derivation, `origin_for_install`); first-run seeding stamps the shipped
  starter agents `platform`/`curated` — scoped to the starter tree only.
- Backfill is VIRTUAL, not a disk pass: a record-less dir falls back to
  `presumed_owner` — the Step-1 layer rule restated as an owner — so checkouts
  and old installs behave exactly as before, with zero migration and no git
  noise in `v2/agents`.
- One deliberate improvement over Step 1: on a DESKTOP, "local" stays among a
  signed-in caller's identities — signing in adds an identity, never subtracts
  one, so the operator's checkout agents remain publishable after sign-in.
  On hosted nothing loosens: a stranger acts only as their account.
- `agents.list` rows now carry `origin` next to `mine`; publish refuses
  installed/curated provenance distinctly ("its author is the only one who can
  ship a new version"); packers and the validator's file listing EXCLUDE the
  record (a bundle carries the author's files; the installer stamps the copy).

### Step 3 — hosted visibility flips to default-private — DONE (uncommitted)
- On hosted: your list = your drafts + your installs + curated. The shared
  catalogue is no longer listed wholesale — an account sees a shared agent
  only when its owner is `platform` or themselves; anything else (a migrated
  desktop dir, a stray copy) is INVISIBLE, not refused. The operator (machine
  token) still sees everything. Enforced in `_current()`, the same choke point
  every read uses — no per-surface checks.
- Record-less shared dirs are presumed `platform` on hosted, so today's EFS
  catalogue keeps rendering exactly as before; the filter bites only on dirs
  whose record says they belong to someone else.
- Ownership is resolved ONCE at scan time onto `AgentSpec.owner/origin`
  (`_with_ownership`), so visibility and `mine` are dict lookups on hot paths,
  never disk reads.
- Curated set = packages installed by the seed job (the Docker entrypoint
  already installs .agentpkg files on boot; installs now stamp records).
- Desktop: zero change — the filter is hosted-only; even a foreign record
  stays VISIBLE on desktop (ownership gates publishing there, never sight).
- HOSTED ≠ ENDORSED (found live: publishing web=true put the agent in every
  user's sidebar). Web-synced copies are re-stamped `origin=web-app`
  (`sync_web_app`): still resolvable — the /apps URL and app connections work —
  and Store-listed, but `registry.listed()` keeps them out of every sidebar
  except the operator's and anyone who installs their own copy. `curated`
  is reserved for what the operator deliberately seeds.
- STILL OPEN from this step: the workspace/chat-path audit (always derive
  from the tenant root). Most already do via the account contextvar; the
  sweep belongs with Step 5's statelessness audit.

### Step 4 — one login story, delete the leftovers — DONE (uncommitted)
- ONE identity: the per-connection session token (contextvar), with exactly one
  ambient form of the SAME value — `AGENTD_SESSION_TOKEN`, for paths with no
  connection at all (the offline CLI `bundle roster …`, CI).
- DELETED from `platform_session_token`: the `AGENTD_MODEL_PROXY_KEY` /
  `AGENTD_MODEL_GATEWAY_KEY` / `config.model_proxy.api_key` fallbacks. Those
  are BILLING credentials (who pays for inference — model_proxy.py still reads
  them for that, unchanged); treating them as identity let a machine key
  publish as nobody in particular. Pinned by
  `test_billing_credentials_are_not_identity`.
- Stale comments fixed: `_account_agents_overlay` and the registry docstring
  no longer claim the overlay is "None on every desktop install" — it is None
  when the CONNECTION has no account, which a signed-in desktop socket has.
- `signin-local-mode-plan.md` stamped STALE (its `auth.login` design was built
  and reversed on the same branch, commit 264be88).

### Step 5 — scale-out readiness — AUDIT DONE; Postgres gated on real scale

**The audit (done 2026-08-11).** Every durable write the daemon makes, and
where it roots:

| write | root | replica-safe |
|---|---|---|
| memory / autonomy / auth / resources `.sqlite` | `state_dir` | yes (on /data) |
| installed_bundles.json, registry_trust.json, downloads/ | `state_dir` (per-account copy when the gateway scopes the marketplace) | yes |
| agent definitions + overlays, workspaces, transcripts | `agents_dir` / `user_state` per account | yes |
| gateway.json, config, .env | `runtime_paths` → AGENTD_HOME | yes |
| ownership records (`.agentd-meta.json`) | inside the agent dir | yes |
| logs | stdout → CloudWatch | yes |
| publish/unlist staging | `tempfile` | ephemeral by design |

Swept for escapes: no `Path.home()` / cwd / absolute-path WRITES outside the
`runtime_paths` seam (the `Path.home()` hits are the AGENTD_HOME default
itself, read-only cache probes, and guard comparisons). On the hosted task
AGENTD_HOME=/data (EFS), so **a daemon replica may die at any moment and
nothing durable is lost** — the Step-4 login model already made sessions
per-connection, so there is no session store to lose either.

**Honest gaps, all gated on running N>1 (none block today's single replica):**
1. **accounts SQLite on EFS** — single-writer; THE blocker for scaling the
   accounts service. The port is mechanical when needed: all 26 query sites go
   through ONE `_db()` contextmanager (`accounts/app.py`), and the path is
   already an env seam (AGENTD_ACCOUNTS_DB). Port = swap the factory for a
   Postgres driver + dialect pass over the SQL + `aws_db_instance` + a one-shot
   sqlite→pg copy. The daemon needs ZERO changes — it talks HTTP.
2. **Two daemon replicas writing one tenant's EFS files concurrently** — needs
   per-tenant connection affinity or a lease. Design when replicas are real.
3. **`auth.sqlite` (the simple_login credential vault) is daemon-global** — on
   a hosted daemon that would share a vault across accounts. Partition it like
   memory (accounts.memory_partition) before offering simple_login hosted.
4. **In-flight turns die with a replica** — accepted; the client reconnects,
   transcripts are already on disk.

Provisioning RDS costs money and is applied by the operator; doing it before
replicas exist buys nothing. When scale arrives, this section is the runbook.

---

## 6. What we deliberately do NOT do

- **No forked builds.** One binary, one code path; deployment shape comes from
  config (the standing one-shape rule).
- **No per-surface refusals.** Availability and ownership are enforced at the
  registry choke point, the same pattern `withheld_reason` already proved.
  A list of "places that must also check" is a list somebody forgets to extend.
- **No editable installs.** An installed agent is a reference to an immutable
  published version. "I want to change it" = fork it into a draft (explicit,
  later feature), never edit-in-place.
- **No premature Postgres / multi-replica work.** Steps 1–4 are correctness
  and product shape; Step 5 is scale, and it waits until scale is real.

---

## 7. Open questions (decide before Step 2)

1. ~~Metadata file name/shape (`.agentd-meta.json`?) — and it must be excluded
   from `.agentpkg` packing.~~ DECIDED with Step 2: `.agentd-meta.json`,
   `{owner, origin, source?{id, version}}`; excluded in `bundle_io` (both
   packers converge there) and in the validator's file listing.
2. ~~Does the platform's curated `main` / `agent-builder` stay special-cased in
   seeding, or become ordinary curated packages too?~~ DECIDED: ordinary
   curated packages — see "Our agents" in §3. `main` keeps its synthesized
   fallback (a daemon with zero packages still needs a brain), but that is a
   bootstrap detail, not an ownership exception.
3. When a desktop user signs in, do the agents they authored while signed out
   migrate to their account, or stay local-owner? (Proposal: stay; offer a
   one-time "claim" action later.)
