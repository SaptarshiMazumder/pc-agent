# Multi-tenant marketplace — implementation plan

Turning the marketplace from "our agents, one shared daemon" into a platform where **anyone
publishes** an agent, **every user gets their own isolated instance of it**, and a creator chooses
how it reaches people: **installed in agentd, downloaded as an .exe, or opened at a hosted URL**.

Status of this document: **plan only, nothing here is built.** Phases are ordered by dependency,
not by appetite. Each phase ends in something shippable and verifiable.

Ledger convention: `[ ]` not started · `[~]` in progress · `[x]` done. Mark items as they land and
keep the "verified" notes — a step is done when its check passes, not when its code exists.

---

## 0. Where we actually are (verified against live dev, 2026-08-07)

Far more exists than the gap suggests. The three delivery modes are already **mechanically**
possible; what is missing is that nothing publishes, advertises, or isolates them.

| Capability | State | Evidence |
|---|---|---|
| Signed public registry | **works** | S3 bucket, HTTPS 200, 2 signed bundles, ed25519 + sha256 verified client-side |
| Install into agentd | **works** | `marketplace.catalog/install/uninstall` RPCs, Store UI, `agentd install` |
| Publish (single publisher) | **works** | `agentd bundle publish` + `publish-registry.yml` (committed, undeployed) |
| **Agent → .exe** | **works** | `npm run gen:app -- <id>` → `dist:app` → `"<Name> Setup <ver>.exe"`. Generic; accepts a third-party `.agentpkg` via `--pkg`, no source needed |
| **Agent → hosted URL** | **works** | daemon serves `/apps/<id>/`; `app_hosts` maps a vanity hostname to an agent; PUBLIC method tier for unauthenticated access when `[app] public = true` |
| Scoped connections | **works** | `scope=agent:<id>`, stable/host/public tiers (docs/PROTOCOL.md) |
| Plugin sandbox | **works** | `SubprocessPluginSandbox`: a child process per untrusted tool call — no runtime handles, allowlisted env, redacted config, audit-hook guard, rlimits. On for the hosted daemon (`AGENTD_SANDBOX_PLUGINS=1` + `AGENTD_SANDBOX_BACKEND=subprocess`); adversarial tests spawn real children and assert each escape is refused |
| Per-creator trust | **works** | index schema 2: root-signed roster, per-creator bundle signatures, revocation, replay bound. `agentd bundle roster add/revoke/show/publish`, `publish --publisher-id`. Schema 1 still parses |
| Licensing | **seam only** | `issue_license` / `parse_license` / `entitled_skus`, `LicenseEntitlement` — unused end to end |
| Payments | **seam only** | `NullPaymentProvider`; creator accrual exists in the ledger |
| **Per-user isolation** | **absent** | this is the blocker — see below |

`figure-creator` already declares `[app]`, so it can become an exe or a hosted URL today.

### The blocker, precisely

`Gateway` is a **singleton** holding `self.config`, `self.service` and `self.marketplace`, and
`config.state_dir` / `agents_dir` / `workspace` / `plugins_dir` are process-global. `platform.connect`
binds **the whole install** to one account — a single-user model, which is correct on a desktop and
wrong on a server. There is no tenant concept anywhere in the codebase.

Consequence today: on the hosted daemon, one visitor's install is visible to and removable by
everyone, and everyone shares one chat history, memory database and workspace.

**Desktop cloud mode is already isolated** — that daemon runs on the user's own PC. This entire
problem is exactly one process: the ECS `daemon` service.

### Why the order is forced

Publishing strangers' code into a process where one filesystem holds every user's files is the
actual risk — not the signing, not the money. So:

```
tenancy  →  sandbox  →  per-creator trust  →  delivery modes  →  entitlements
   1          2                3                    4                5
```

Phases 4 and 5 are independently useful and could be pulled forward for **our own** agents, but
third-party publishing must not open before 2.

---

## Decisions (locked 2026-08-07)

**D1 — Isolation: per-user directories AND a real sandbox, both now.**
Filesystem separation per account, plus hardening the dormant sandbox seam into a real backend, so
untrusted plugin code is contained from day one rather than behind an interim trust gate.

**D2 — Publish trust: per-creator keys + a client trust list.**
Each creator holds their own keypair and signs their own bundles. The roster of trusted creator
keys is itself signed by the platform root key, so clients still pin exactly one key (§3).

**D3 — Money: entitlements and licensing plumbing only, `NullPaymentProvider` stays.**
Paid bundles, entitlement checks, signed `.lic` files and creator accrual all work end to end;
no money moves. A real rail (Merchant-of-Record) becomes one adapter, later.

**D4 (mine, routine) — Artifacts live beside the registry.** `.exe` installers and `.agentpkg`
files share the registry bucket. No second distribution system.

**D5 (mine, routine) — Hosted app URLs stay path-based** (`/apps/<id>/`) until a domain exists;
`app_hosts` already supports vanity hostnames when it does.

---

## Execution order (narrowed 2026-08-07)

Scope cut by the user: **get the web version running and the app UI launching inside agentd.**
`.exe` distribution (Phase 4.3) is explicitly deferred until after that.

| # | Step | Size | Depends on | Verifiable when |
|---|---|---|---|---|
| **0** | Ship what is already committed | hours | — | web loads on the current ALB, Store lists + installs |
| **1** | App UI **embedded** in agentd (apps-plan P4) | S–M | none | click an agent → its own UI renders inside the shell, desktop and web |
| **2** | Tenancy (Phase 1 below) | XL | 0 | two accounts cannot see each other's agents, chats or files |
| ~~**3**~~ | ~~Sandbox (Phase 2 below)~~ **DONE 2026-08-08** | L | 2 | adversarial tests fail closed ✔ |
| ~~4~~ | ~~Per-creator trust (Phase 3)~~ **DONE 2026-08-08** (publish API still open) | M | 3 | a second keypair can publish ✔ |
| 5 | Delivery modes minus exe (Phase 4.1/4.2/4.4) | M | 2 | Store shows Install / Open |
| 6 | Entitlements (Phase 5) | M | 4 | a paid bundle refuses to install unlicensed |
| — | `.exe` (Phase 4.3) | M | 5 + code-signing decision | deferred by the user |

**Why 1 before 2**, even though tenancy is the bigger prize: the embed has no tenancy dependency,
it is days rather than weeks, and it exercises the scoped-connection path with a real app *before*
multi-tenancy complicates that path. Its URL shape (`/apps/<id>/?scope=…&token=…`) does not change
under tenancy — the token simply starts resolving to a tenant — so there is no rework. They are
independent and could swap; this way the app experience exists while tenancy is being built.

### Progress log

**2026-08-07 — Step 1 DONE, Step 2 foundation done, gateway wiring NOT done.**

Landed:
- `AppView.tsx` + `view: 'app'` + `openAgentApp()`; `AgentView.openApp()` now embeds for
  `mode: "browser"` and still opens a real window for `mode: "window"`. CSS modifier `app-embed`.
  Verified: `frame-src` in a hosted build does gain the daemon's http origin; ui typecheck clean;
  web bundle builds.
- `agent_runtime/infrastructure/tenancy/` — `paths` (validation + containment), `identity`
  (`AccountResolver`, fail-closed), `registry` (`Tenant`, `TenantRegistry`, LRU + idle eviction,
  ref-pinning). Config knobs `multi_tenant` / `tenant_root` / `max_tenants` /
  `tenant_idle_seconds`, env-only. 44 unit tests, full suite 747 green.

**2026-08-07 (later) — Phase 1 CORRECTED AND DONE. The plan below was wrong about the starting
point; read this before implementing anything from it.**

Phase 1 assumed the daemon had "no tenant concept anywhere". That was a misreading. Per-account
isolation was already largely built, by a *different and better* mechanism than the `Tenant` /
`TenantRegistry` I first wrote:

- `infrastructure/accounts.py` resolves a session token to an account **per connection** and pins
  it on a contextvar for the life of the socket (`_handle_conn`), with a short resolve cache.
- `infrastructure/user_state.py` maps that account to `<state_dir>/accounts/<acct>/…`, and
  sessions, workspace and the projects root already route through it.
- `accounts.memory_partition()` namespaces semantic memory by account with no schema change.

So identity, path scoping and the contextvar plumbing all existed. **The `tenancy` package I built
(paths/identity/registry, 44 tests) was deleted** — it duplicated that mechanism, and two ways to
answer "where does this account's data live" is exactly what rots. The registry's per-tenant
Config clones and eviction cap were solving a problem the contextvar approach does not have.

What was ACTUALLY missing was the thing `user_state`'s own docstring called out: *"the agent
CATALOG stays shared"*. True and fine until the marketplace let a user install one — then installs
went to the daemon-global `agents_dir`, so one visitor's install appeared in everyone's list and
their uninstall removed it from everyone's. That is now fixed:

- `user_state.account_agents_dir` / `account_plugins_dir` — `<state_dir>/accounts/<acct>/installed/…`
- `FileAgentRegistry(config, overlay_dir=…)` — two layers: the deployment's curated catalogue
  (shared, read-only) unioned with the caller's own installs (overlay wins on id collision).
  Writes go to the overlay when an account is active. **An account cannot remove a shared agent** —
  without that guard an ordinary uninstall rmtree's the catalogue for every other user.
- `Gateway._marketplace()` is now per-account: the service is composed against a config whose
  agents/plugins/state dirs point into the caller's subtree, so an install writes there and
  `installed_bundles.json` is per-account too.
- Desktop is byte-for-byte unchanged: accounts off ⇒ `account_id()` is None ⇒ no overlay, one
  catalogue, one marketplace service.

21 new tests; unit + integration 999 green.

Still open from Phase 1 (deliberately, and none of it blocks the web version):
- **cron / scheduled runs** are still keyed by `agent_id` only — a per-account schedule has no
  home yet. Refuse or partition before autonomy is offered to hosted users.
- **Uninstall leaves the account's transcripts** under `accounts/<acct>/agents/<id>/`. Deliberate
  (losing history to an uninstall is worse than orphaned bytes), but reinstalling resurrects old
  chats. Wire it to a "delete my data" flag.
- **Disk quota** — nothing bounds a hosted workspace, and EFS bills per GB.
- ~~The plugin **sandbox** (Phase 2) is still the gate for third-party publishing~~ — done
  2026-08-08; per-account directories separate files, and now a subprocess separates processes too.

**2026-08-08 — Steps 3 and 4 done (sandbox + per-creator trust).**

- **Sandbox**: `SubprocessPluginSandbox` behind the existing port. One child per untrusted tool
  call; `backends.build_plugin_sandbox` is the only place a backend is named. On for the hosted
  daemon via two env vars in `variables.tf` — and it takes BOTH, because
  `AGENTD_SANDBOX_PLUGINS` alone wires the seam to the in-process passthrough, which enforces
  nothing and reports no error.
- **Per-creator trust**: index schema 2, root-signed roster, per-creator bundle signatures,
  revocation, replay bound, and `agentd bundle roster` — including `roster publish`, which the
  smoke test proved was missing (a revocation had no way to reach the registry).
- Suite: 1078 unit + integration green. `terraform validate` clean on both root modules.

Still open, unchanged: disk quota, the publish API (Phase 3's second half), delivery modes,
entitlements, and Backend B (gVisor).

### Step 0 — ship the committed work `[ ]`
- [ ] Merge `feature/meter` → `develop`; Deploy rebuilds all five images against the live ALB
      (the deployed web image still has a dead hostname baked in).
- [ ] Confirm the served page references the current ALB and the Store lists both bundles.

### Step 1 — app UI inside agentd (apps-plan P4) `[ ]`
Today `AgentView.openApp()` calls `platform.openAppWindow?.()` or `window.open()` — the app always
leaves the shell. The daemon side is already done: `_agent_app()` advertises `{title, url, mode}`,
`appLaunchUrl()` mints a tokenized scoped URL, and `/apps/<id>/` serves the files.

- [ ] New `AppView` inside `PageShell`: an `<iframe src={appLaunchUrl(app, id)}>` filling the body,
      with the agent's name in the header and an "open in a window" escape hatch.
- [ ] Route it like the other views (`view === 'app'` in App.tsx) and make the agent page's
      **Open** button switch views instead of popping a window.
- [ ] Honour the author's `mode`: `"window"` keeps today's dedicated-window behaviour, `"browser"`
      now means *embedded*. The author's declaration keeps meaning something.
- [ ] Sandbox the iframe (`allow-scripts allow-same-origin` at most) — an app agent is third-party
      code inside our shell, and it must not be able to script the parent.
- [ ] **CSP**: the hosted web build must allow the daemon origin in `frame-src`. The vite plugin
      already widens `connect-src|img-src|media-src|frame-src` with the API origins — verify, do not
      assume, since that regex was silently broken once already.
- [ ] Electron: confirm an iframe to the daemon origin is permitted by the renderer's CSP and
      `webSecurity` settings.
- [ ] Cross-origin note: on the hosted deployment the shell is on `:80` and the daemon on `:8787`,
      so the iframe is cross-origin. Token and scope already travel in the URL, which is why this
      works without postMessage plumbing.
- [ ] Verify with `figure-creator` (declares `[app]`) on desktop, then on web.

### TLS — a real dependency for "the web version", not a nice-to-have `[ ]`
Everything is HTTP today, so session tokens cross the internet in cleartext. Fine for you testing;
not fine the moment anyone else signs in.
- [ ] A domain (~$12/yr), an ACM cert (free), an ALB `:443` listener; the port listeners become
      redirects. `alb.tf` was deliberately structured so this lands additively.
- [ ] It also ends the hostname churn that `hibernate` causes — a stable domain means the web image
      stops needing a rebuild after every wake.

---

## Phase 1 — Tenancy on the hosted daemon `[ ]`

**Goal:** two people using the hosted app cannot see, break, or even detect each other's agents,
installs, chats, memory or files. Desktop behaviour is byte-for-byte unchanged.

**Size: XL.** This is the phase that pays for the rest. Budget the most time here.

### 1.1 The tenancy switch `[ ]`
- [ ] `config.multi_tenant: bool` (`AGENTD_MULTI_TENANT`), **default false**. Single-tenant is the
      desktop and must stay the untouched path — every change below is inert when the flag is off.
- [ ] `config.tenant_root: Path` (`AGENTD_TENANT_ROOT`, default `<AGENTD_HOME>/users`).
- [ ] Set both on the ECS `daemon` service via `local.computed_env` (services.tf) — the same
      mechanism that carries the registry env.

### 1.2 Per-connection identity `[ ]`
Today `platform.connect` binds the install; multi-tenant needs identity **per connection**.
- [ ] Accept a session token on the WS handshake (`?session=` and/or a `connect` frame) and resolve
      it to an `account_id` via the accounts service `/resolve`.
- [ ] Cache resolutions (TTL ~5 min) — copy the shape of `ingest/app.py::_account_for`; `/resolve`
      is already the platform hot path (DEF-6) and must not take a call per WS frame.
- [ ] **Fail CLOSED here**, unlike ingest: an unresolvable token on a multi-tenant daemon must be
      refused, never silently downgraded to a shared tenant. (Ingest fails open because losing a
      metric is cheaper than losing the outage report; here failing open leaks data.)
- [ ] `platform.connect` stays the desktop path and is **rejected** when `multi_tenant` is on.

### 1.3 The tenant object `[ ]`
- [ ] `Tenant`: `account_id` + a `Config` clone whose `state_dir`, `agents_dir`, `workspace`,
      `plugins_dir`, `skills_dir` point under `<tenant_root>/<account_id>/…`, plus that tenant's
      own `service` (tool/agent registry), `marketplace`, installed store, memory and sessions.
- [ ] `TenantRegistry`: `get(account_id) -> Tenant`, lazily built, LRU with idle eviction, hard cap
      (`AGENTD_MAX_TENANTS`). Each tenant loads plugins into memory, so this is a **memory** budget,
      not a money one — measure per-tenant RSS before choosing the cap.
- [ ] `Gateway` routes every request through the connection's tenant instead of `self.*`. Mechanical
      but wide: `self.config`/`self.service`/`self.marketplace` appear throughout gateway.py.
      Keep a `self._default_tenant` so single-tenant is literally "the registry with one entry" —
      one code path, not two.

### 1.4 Seeding a new tenant `[ ]`
- [ ] First touch creates the directory tree and installs the seed agents (reuse the existing
      `preinstalled_bundles` flow, so it is the ordinary marketplace path — not a second installer).
- [ ] Every tenant needs `main`. Verify agent-id resolution and `MAIN_AGENT_ID` display behave.
- [ ] Decide and document what happens to today's shared `/data/agents` — proposal: leave it as the
      single-tenant seed and never migrate; hosted users start clean.

### 1.5 Anonymous and public-app connections `[ ]`
- [ ] A `public = true` app agent admits unauthenticated connections. Give each an **ephemeral
      tenant** discarded on disconnect (no persistence, no cross-talk).
- [ ] Rate-limit ephemeral tenant creation — it is an unauthenticated allocation path.

### 1.6 Things that quietly assume one user `[ ]`
Each needs an explicit answer, and each is a silent data-leak if missed:
- [ ] cron / scheduled agent runs — the scheduler must iterate tenants (or refuse in multi-tenant)
- [ ] `list_sessions`, memory sqlite, resources store, projects store (all under `state_dir` — should
      follow for free, **verify**)
- [ ] file/artifact serving paths (`show_files`, canvas, `/apps/<id>` static assets)
- [ ] the credential vault and MCP subprocess env — MCP servers inherit the daemon's environment
- [ ] telemetry: `account_id` stays a **property**, never a dimension (cardinality → cost)

### 1.7 Verification `[ ]`
- [ ] Integration test: two tenants, each installs a different bundle; neither sees the other's
      catalog, sessions, memory or files; uninstall by one does not affect the other.
- [ ] Escape test: path traversal in an agent id / bundle id cannot climb out of the tenant root.
- [ ] Desktop regression: full unit suite green, `multi_tenant=false` path unchanged.

---

## Phase 2 — Real plugin sandbox `[x]` (2026-08-08)

**Goal:** third-party plugin code cannot read another tenant's files, exfiltrate secrets, or reach
the network unless its capabilities say so. **Size: L.**

- [x] **Backend A — subprocess isolation** (`infrastructure/tools/sandbox/subprocess_backend.py`):
      one child process per tool call, cwd pinned to the run's workspace, allowlisted env, POSIX
      rlimits, no inherited handles, killed on timeout or abort.
- [x] `CapabilityResolver` grants: `fs` (workspace only), `net` (allowlist, empty by default),
      `secrets` **never**, plus ceilings from `config.sandbox_limits`.
- [x] `Guard(Sandbox(inner))` and the `classify.py` tiering are unchanged; approval stays separate.
- [x] `AGENTD_SANDBOX_PLUGINS` on by default when `multi_tenant` is on, and set EXPLICITLY on the
      hosted daemon (which does per-account isolation without that flag) together with
      `AGENTD_SANDBOX_BACKEND=subprocess`.
- [x] Adversarial tests (`tests/unit/test_plugin_sandbox_subprocess.py`, 30): each spawns a real
      child that really tries it — read another account's directory, write outside the workspace,
      read the daemon's own config, open a socket, spawn a helper, read provider keys from the
      environment or out of `ctx.config`, forge a protocol frame with `print`, hang past the
      deadline. All refused, and the legitimate case (its own workspace) still works.
- [ ] **Backend B — gVisor** (Linux/Fargate only) behind the same port. Not built; the port is
      ready for it and `backends.py` is the one place it would be named.
- [ ] Measure the per-call overhead on a real workload. `sandbox_run_ms` is emitted; nothing has
      been profiled under load yet.

### Three things about this that should not be discovered later

**It is an interpreter-level control, not a kernel one.** The child's guard is a `sys.addaudithook`
hook. A plugin shipping a compiled extension module runs machine code that never consults it, and
that is a real escape. Denying `ctypes` closes the easy version. The kernel-level answer is
`sandbox_child_uid` (POSIX, daemon as root, tenant dirs owned appropriately) or Backend B — both
available behind the same port, neither turned on.

**What survives a total escape is the honest measure**, and it is a lot less than before: a process
with no provider keys in its environment, no live handles to anything, a redacted config, and a
working directory inside the caller's own workspace. The residual risk is reading files owned by
the same OS user — which is what the uid drop closes.

**Model calls are inverted, not excepted** (2026-08-08). No network and no credentials meant an
untrusted plugin could not call a model — which would have banned the interesting half of the
marketplace and created steady pressure to switch the sandbox off. So the tool ASKS and the host
performs, checks, clamps and meters against the account running the agent. `net_allowlist` stays
empty; a sandboxed tool can obtain a completion and still cannot open a socket. See
[sandboxed-model-calls-plan.md](sandboxed-model-calls-plan.md).

**Deny beats allow, except for the grant.** The child must be able to read the interpreter's path
roots or it cannot import at all, and in a source checkout that root is the repository, which holds
`.env`. So the daemon names its own secret paths (`config_path`, `state_dir`, `tenant_root`, the
dotenv files) as denied, and the run's granted workspace beats the denial — otherwise every account
would be locked out of its own files, since a workspace lives inside the tenant root.

---

## Phase 3 — Per-creator trust `[x]` trust model (2026-08-08) · `[ ]` publish API

**Goal:** many publishers, without every installed client needing a new pinned key. **Size: M.**

### The design that avoids re-pinning
`index.json` schema **2**:

```jsonc
{
  "schema": 2,
  "publishers": {                 // the TRUST ROSTER
    "roster": [ { "id": "acme", "name": "Acme", "key": "<b64 ed25519 pub>", "added": "…" } ],
    "revoked": [ "badactor" ],
    "issued": "2026-08-07T…Z",    // monotonic; clients reject an older roster than they have seen
    "sig": "<roster signed by the PLATFORM ROOT KEY>"
  },
  "bundles": [ { "id": "…", "publisher_id": "acme", "sha256": "…", "sig": "<by acme's key>" } ]
}
```

Clients still pin **exactly one** key — the platform root — and derive creator trust from the
signed roster. No client re-pinning, no rebuild of installed apps, and revocation is a roster edit.

- [x] `RegistryClient`: verify the roster with the pinned root key on **index fetch** → id→key map
      → verify each bundle against **its** creator's key. Unknown or revoked creator: refused. A
      roster that does not verify refuses the **whole registry**, not one bundle — a store that
      renders cards from a listing it has already distrusted will happily offer the install.
- [x] Schema 1 still parses (`SUPPORTED_INDEX_SCHEMAS`). Migration is deliberately boring: make the
      existing publisher key the root key, and carried entries with no `publisher_id` keep
      verifying against it. Nothing already published needs re-signing.
- [x] `index_builder`: emits schema 2, stamps `publisher_id`, and — the important part — does NOT
      re-sign carried entries in schema 2. Schema 1 re-signed every carried entry with the
      publishing key, which in a multi-creator registry would invalidate every other creator's
      bundles on each publish, silently.
- [x] Creator keys: `agentd bundle keygen`; the private key never leaves the creator. Publishing
      refuses up front on unlisted creator / revoked creator / listed-but-wrong-key, because all
      three produce the same symptom — the store lists it and every download fails.
- [x] `agentd bundle roster add|revoke|show|publish`; the root key is only ever needed by these.
- [x] Replay bound: `RosterMemory` (`<state_dir>/registry_trust.json`) remembers the newest
      `issued` accepted per registry and refuses a roster that goes backwards.
- [x] Known limitation, documented in `domain/bundle.py` and `trust.py`: the roster and each bundle
      are signed, the index as a whole is not. A rewritten index can REMOVE or REPLAY an entry; it
      can never forge one. `issued` + client memory bounds the replay, and a first fetch has
      nothing to compare against. A signed index manifest is the eventual fix.

**Found by the smoke test, worth keeping in mind:** a revocation could not be delivered. Adding a
creator rides along with their first publish, but the creator being revoked is exactly the person
who can no longer publish — so the roster edit sat in a local file with no way to reach the
registry. Hence `agentd bundle roster publish`, which pushes only `index.json` and touches no
artifact. Revocation stops NEW installs; it is not a remote uninstall, and the command says so.

### The publish API `[ ]`
- [ ] **Home: the accounts service**, not a new ECS service (a sixth always-on task is poor value)
      and not the daemon (which any web visitor reaches). Accounts already has auth and is the
      identity authority. Confirm at implementation.
- [ ] `POST /registry/publish`: authenticated, multipart `.agentpkg`, verified against the
      submitter's roster key, then written to S3 and the index rebuilt.
- [ ] **The service never needs the root private key** — creators sign their own bundles, and the
      roster is signed offline in CI. This is the main security win of D2.
- [ ] `creator` flag / roster membership on the account; default off.
- [ ] Per-account publish rate limits and a size cap.

---

## Phase 4 — Three delivery modes `[x]` (built 2026-08-09, uncommitted)

**Goal:** the creator chooses how their agent reaches people; the Store shows the choice.
**As built, the shape changed from the sketch below:** `install` needs no declaration (a bundle
IS the thing a daemon installs), and the exe pipeline landed in the publish Lambda rather than
CI — so `[delivery]` has exactly two keys.

### 4.1 Declaration `[x]`
In `agent.toml` (author-facing) or `bundle.toml`'s `[bundle.delivery]` (publisher override,
whole-table):
```toml
[delivery]
web = false                        # Open-in-browser via the hosted deployment (requires [app];
                                   # OPT-IN — running on our infra is never inferred)
exe = true                         # per-agent installer built at publish (the old default)
```
- [x] `DeliveryModes` on `BundleManifest` + `RegistryEntry`, tolerant parse (domain/bundle.py);
      serialized by `bundle_io._manifest_toml`; precedence in `packer._delivery`.
- [x] `web = true` without `[app]` is refused at pack time AND at intake (400).
- [x] Intake skips the stub build for web-only bundles and stamps
      `"delivery": {web, exe}` into the index row.

### 4.2 Hosted URL `[x]`
- [x] Index-level `web.host` (like `engine`): stamped by the publish service from `WEB_HOST`
      (terraform: `local.publish_web_host` = the daemon's public url); parsed into
      `RegistryIndex.web_host`.
- [x] Install on first open: hosted `GET /apps/<id>` for an unknown id schedules
      `marketplace.sync_web_app(id)` (installs into the SHARED catalogue — /apps static serving
      is unauthenticated, one copy serves every visitor) and serves a self-refreshing page.
      `sync_web_app` re-checks the author's web opt-in, so a URL guess cannot conscript the host.
      Entry-page opens re-check the registry for updates (throttled, 300s).
- [x] Store card gains **Open in browser** via `webUrl` (joined daemon-side, never client-built).
- [ ] Per-visitor tenant for app STATE remains the scoped-WS story (unchanged here).

### 4.3 Downloadable .exe `[x]` (landed earlier via the publish Lambda, not CI)
- [x] The publish service builds the stub per publish; installer rows signed separately.
- [ ] **Code signing is still a real blocker for strangers' installers** (SmartScreen).

### 4.4 Store UI `[x]`
- [x] Cards render exactly the doors the author opened (Install / Open / Download).

---

## Phase 5 — Entitlements and creator revenue `[ ]`

**Goal:** paid agents work end to end with no money moving. **Size: M.**

- [ ] Agent SKUs as products in accounts (the credit-pack catalogue already models products).
- [ ] Purchase (via `NullPaymentProvider`) → accounts issues a signed `.lic` (`issue_license`
      already exists) → client stores it → `LicenseEntitlement` gates the plugins.
- [ ] `marketplace.install` refuses a paid bundle without an entitlement; the Store shows **Get**.
- [ ] Creator accrual already splits at purchase (`creator_micros`) — surface a creator earnings
      view.
- [ ] Offline grace: a licence must survive a few days without reaching accounts, or an outage
      locks paying users out of software they own.
- [ ] Payout is deliberately **out of scope** — no rail, no entity.

---

## Cross-cutting

**Telemetry.** New counters: `tenant_active` (gauge), `tenant_created_total`,
`publish_total{outcome}`, `install_total{outcome,delivery}`, `sandbox_denied_total{capability}`.
`account_id` and `publisher_id` stay properties, never dimensions.

**Cost.** Tenancy costs memory, not money (one process). The sandbox costs CPU per tool call.
Neither adds a Fargate task. The publish API rides on accounts. This plan should not move the bill.

**Docs.** `docs/PROTOCOL.md` needs the tenancy rules; a `PUBLISHING.md` for creators (keygen,
`bundle.toml`, delivery modes, what review means).

**Rollout.** Every phase is flag-gated (`AGENTD_MULTI_TENANT`, `AGENTD_SANDBOX_PLUGINS`, schema-2
detection). Dev first, and the flags stay off on desktop permanently.

---

## Risks, in the order they will actually hurt

1. **Phase 1 is wide, not deep.** The danger is a missed singleton — one forgotten global path and
    a user reads another's files. Mitigation: enumerate every `state_dir`/`agents_dir`/`workspace`
    reader (§1.6) and write the two-tenant leak test *first*.
2. **Sandbox overhead.** If it doubles tool latency it gets disabled, and then Phase 3 opens on an
    unprotected runtime. Measure before enabling by default.
3. **Unsigned .exe distribution.** Strangers' installers flagged as malware damages trust in the
    whole marketplace faster than any feature adds to it.
4. **Roster downgrade.** Documented limitation; needs the monotonic `issued` check to be real, not
    aspirational.
5. **Memory per tenant.** Plugin loading per tenant may be heavier than expected; the cap and
    eviction policy are load-bearing, so measure early.

## Open questions

- Tenant identity: account id, or a per-account workspace id (a user with several workspaces)?
- Do hosted tenants get a disk quota? EFS is billed per GB and nothing bounds a workspace today.
- Does a creator publish from the app (needs Phase 1 + 3) or only via CLI/CI?
- Review policy for `delivery.app` — a hosted agent runs on our infrastructure and our bill.

## Prerequisite, not part of any phase

- [ ] Merge `feature/meter` → `develop` so the committed marketplace work actually ships (the web
      image still has a dead ALB hostname baked in).
- [ ] `terraform -chdir=v2/infra/github-oidc apply` before the first CI publish.
- [ ] Locate the publisher private key — the registry is signed by `gYM/XoS5…` and `bundle publish`
      refuses any other key. Losing it forces a root rotation, which under D2 is far more expensive
      than it is today, because the roster is anchored to it.
