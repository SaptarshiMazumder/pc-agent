# The Agent Rulebook — what a user-created agent must obey, and who enforces it

STATUS: audit result 2026-08-14; ENFORCEMENT APPLIED same day (uncommitted, daemon restart
needed). What landed: ONE central policy table `agent_authoring/domain/rulebook.py` (every
finding code, severity override + pack/publish gates — validate/package/publish all read it);
new `portability_rules.py` (WIDE_WRITE_ROOTS, EXEC_ON_WEB, WEB_REQUIRES_LOCAL,
HEARTBEAT_WITHOUT_AUTONOMY) + WORKSPACE_NOT_SHIPPED; runtime refusals — reserved/shaped ids
(`domain/agent.invalid_new_agent_id`, all creation paths), ownership stamp fail-closed
(create_from rolls back), installed agents' write_roots clamped to their own folder
(`agent_service._installed_write_clamp`); create_agent auto-validates in its result; the
`agents.create` RPC births the same skeleton (version + IDENTITY.md); publish refuses
web-app origin + rulebook blockers; SKILL.md teaches the hosted fence ("Design for hosted",
[tools.fs] grammar) and AGENTS.md carries the ship rules. Pinned by
tests/unit/test_agent_rulebook.py (+updated package/publish/skill tests); full suite green.
Still open from the gap list: 7 (heartbeat account), 8 (cron budget), 9 (bundle content
scan/pip), 10 (resources partition), 12 (PROTOCOL.md generation), 15, and server-side
validation (gap 4) — the builder gate is closed, the CLI/S3 path still is not.

Four-agent deep dive over creation paths, publish/install,
the plugin sandbox contract, and hosted runtime constraints. This file is the single list the
agent-builder must teach and the platform must enforce. Enforcement tags:

- **[RUNTIME]** — enforced by the daemon at run time (refusal / fail-closed). Cannot be dodged.
- **[BUILDER]** — enforced by agent-builder's own tools at write/pack/publish time
  (create_tool refusals, validate_agent ERRORs, package_agent blockers). Dodgeable by any
  other client (CLI, raw RPC).
- **[SERVER]** — enforced by the publish service / installer. Authoritative for marketplace.
- **[TAUGHT]** — prompt/skill text only. Nothing checks it.
- **[NOWHERE]** — a real rule that nothing teaches or enforces. These are the gaps.

---

## A. Identity & layout (birth)

| # | Rule | Enforced |
|---|------|----------|
| A1 | An agent is a directory with `agent.toml`; that file's presence IS the definition test (`file_registry.is_definition_dir`) | [RUNTIME] |
| A2 | id: lowercase, alnum/`-`/`_` only (`file_registry._valid_id`); slugged from free text | [RUNTIME] |
| A3 | `main` is reserved — cannot be created, updated, or removed | [RUNTIME] |
| A4 | Layer-aware collision: a new id may not shadow any existing agent in shared catalogue OR the caller's overlay | [RUNTIME] `file_registry.create_from:696` |
| A5 | Placement is the registry's choice alone: signed-in → account overlay, else shared catalogue (`_write_target`). Never pick your own path | [RUNTIME] |
| A6 | Ownership is DATA: `.agentd-meta.json` stamped at birth BY THE RUNTIME (owner + origin). Never author it; packers exclude it; installer re-stamps | [RUNTIME] (but write failure is silent — see GAP-13) |
| A7 | Required content: `agent.toml` with `name` + `version`, and `IDENTITY.md` (validator ERROR `NO_IDENTITY` without) | [BUILDER] — the RPC path violates this, see GAP-1 |
| A8 | `workspace/` is auto-created; `sessions/` is runtime-managed. Both are USER DATA (`USER_DATA_DIRS`), never definition | [RUNTIME] |

## B. Data placement (tenant-fence consequences)

| # | Rule | Enforced |
|---|------|----------|
| B1 | Ship data ONLY in definition dirs (`templates/`, `skills/`, `plugins/`, `ui/`, `data/`…). NEVER in `workspace/` — it is excluded from the package, excluded from the hosted read grant, and is a DIFFERENT EMPTY dir per user | [RUNTIME] fence + pack exclusions; [NOWHERE] as teaching — SKILL.md never says it |
| B2 | Hosted reads = own account root + own workspace + THIS agent's definition view + shared catalogue/plugins. Nothing else exists (`tenant_scope` → `check_read`) | [RUNTIME] |
| B3 | Hosted writes clamped to own account root + workspace (`write_clamp`) | [RUNTIME] |
| B4 | Sessions/transcripts are in NO read grant ever; only session RPCs behind `may_observe` | [RUNTIME] |
| B5 | Never hardcode absolute machine paths; use `<agents_dir>`/`<agent_dir>` tokens (unknown token DROPS the entry — narrows, never widens) | [RUNTIME] expansion; [TAUGHT] partially |
| B6 | Workspace starts EMPTY for every new hosted user — no first-run seed, no author fixtures | [NOWHERE] as teaching |

## C. Tools & grants (agent.toml)

| # | Rule | Enforced |
|---|------|----------|
| C1 | `[tools] allow`/`deny`, deny beats allow; private tools implicitly allowed; grant `exec`+`process` as a pair (`COMPANION_TOOL_MISSING` WARN) | [RUNTIME] deny-wins; pair is WARN only |
| C2 | `[tools.fs] write_roots`/`deny` with tokens; an agent cannot widen the platform clamp (`RunContext` separates agent declaration from platform boundary) | [RUNTIME]; grammar [NOWHERE] in SKILL.md — see GAP-2 |
| C3 | Design for NO SHELL on hosted: `exec` refuses any fenced run. Agent needs exec → `requires_local = true` → invisible on hosted | [RUNTIME] |
| C4 | Installed agents' definitions are read-only (`protected_paths`); an agent must not need to self-modify (write_denies its own `<agent_dir>`) | [RUNTIME] (in-process only; not an OS ACL) |
| C5 | Tool names must exist / be real — nothing validates `[tools] allow` names at creation | [NOWHERE] |

## D. Private plugins (the sandbox contract)

| # | Rule | Enforced |
|---|------|----------|
| D1 | `agents/<id>/plugins/<pid>/plugin.toml` with `id`, `kind ∈ {native,mcp}`, `entry` (native); ≥1 `.py` module; tools only (no prompt sections/skills/mcp servers from the private tier) | [RUNTIME] discovery skips invalid; validator ERRORs |
| D2 | Untrusted on every machine but the author's: marketplace ledger = provenance; agent.toml is never consulted (an agent cannot vouch for itself) | [RUNTIME] classifier, fail-closed |
| D3 | Files: write run workspace + temp only; read definition view only; deny_paths (config, state_dir, tenant roots, env files, credentials) always win | [RUNTIME] child_guard |
| D4 | NEVER spawn processes — denied outright, no declaration exists to ask | [RUNTIME] + [BUILDER] create_tool refuses `subprocess`/`os.system` at write time |
| D5 | NEVER read `os.environ`; secrets = declare names in `[sandbox] secrets`, use `${NAME}` placeholders; values resolved HOST-side, never enter the child (grant.secrets = {} always) | [RUNTIME] env allowlist + [BUILDER] refusal + CI tripwire |
| D6 | NEVER import requests/httpx/aiohttp/urllib/socket; outbound = `infrastructure.net.outbound.fetch` + `[sandbox] net` host list (no `"*"` — silently dropped); operator can only narrow | [RUNTIME] broker + [BUILDER] refusal |
| D7 | Models: `needs_model = True` + `model_kind` + the `self.models`/`oneshot` funnel ONLY. No SDKs, no keys; `api_key=` params silently ignored in sandbox. Without `needs_model` the grant is `()` and every call is refused ON THE BUYER'S MACHINE while working on yours | [RUNTIME] broker + CI `test_no_rogue_model_keys` (no allowlist) |
| D8 | fal/replicate are the only BYOK exceptions, and must say so in the tool description | [TAUGHT] + CI env-key allowlist |
| D9 | Tool contract: non-empty `name`, `description`, JSON-Schema `parameters`, `async execute`; `plugin` key = config key; bare plugin-local imports (folder is sys.path, no package) | [RUNTIME] |
| D10 | Inline rendering ONLY via `ToolResult.artifacts` (declare what you PRODUCED); canvas buttons via `artifact_action`; provider pickers via `provider_options` | [TAUGHT] convention |
| D11 | Native/compiled extensions: `sandbox_allow_native` defaults False; the audit-hook guard is interpreter-level — kernel enforcement (uid/gid) is POSIX-only. Do not ship native binaries | [RUNTIME] partial; shipping unchecked — see GAP-9 |
| D12 | `[sandbox]` in a SHARED `v2/plugins/*` plugin.toml is inert (never stamped, never sandboxed) — moving a tool to shared tier is the laundering path; create_tool warns | [BUILDER] warn only |

## E. Models & config

| # | Rule | Enforced |
|---|------|----------|
| E1 | Nothing hardcoded: model wiring via `[plugins.<pid>.tools.<tool>]` in agent.toml, layered over global config; precedence per-call > agent > config > default; `""` = unset | [RUNTIME] `resolve_tool_model` |
| E2 | Config-only resolution — no AGENTD_* env var changes a model; never invent config keys | [TAUGHT] |
| E3 | `model_kind` defaults live in `KIND_DEFAULT_MODELS`/`config.model_defaults`; text inherits the brain on purpose | [RUNTIME] |

## F. App / UI

| # | Rule | Enforced |
|---|------|----------|
| F1 | `ui/` served at `/apps/<id>`; entry declared in `[app]`; `scaffold_ui` first, never hand-write; relative asset base | [BUILDER] validator `APP_ENTRY_MISSING`/`ORPHANED_UI` |
| F2 | Apps INVOKE, never administer: stable tier = `APP_SCOPED_METHODS` only; the daemon forces the connection's agentId onto every request | [RUNTIME] |
| F3 | Public surface = `[app] public_tools` ∩ agent allow/deny, `[app] public = true` opt-in; chat is NEVER public; 8 concurrent / 256 connections cap | [RUNTIME] |
| F4 | Event payloads nested (`payload.event.type`, not `payload.type`); settle on `agent_end`; sign-in gate BEFORE wiring the socket; pass no agent id | [BUILDER] `EVENT_PAYLOAD_NOT_NESTED`/`UNKNOWN_EVENT` ERRORs |
| F5 | No CSP on app pages: treat the DOM as hostile, never render untrusted content raw, never echo secrets (config reads are redacted for installed scopes; keys are write-only) | [RUNTIME] redaction; CSP [NOWHERE] |

## G. Autonomy

| # | Rule | Enforced |
|---|------|----------|
| G1 | Scheduling ONLY via the `cron` tool (durable store); never exec sleep loops or OS schedulers | [TAUGHT] (+ exec dead on hosted anyway) |
| G2 | Cron runs call `report_outcome` EXACTLY ONCE; skipping marks the run incomplete | [RUNTIME] incomplete-marking |
| G3 | Heartbeat = `heartbeat` interval + `[capabilities] autonomy` + `HEARTBEAT.md` (injected only on ticks) | [TAUGHT]; interval syntax unvalidated |

## H. Publishing & marketplace

| # | Rule | Enforced |
|---|------|----------|
| H1 | Publish only YOUR agents: `registry.owns` + origin ∉ {installed, curated} | [BUILDER] client-side; server re-checks bundle-id ownership only — see GAP-5 |
| H2 | `validate_agent` ERROR-free before publish; `dry_run=false` + `confirm=true` double signal | [BUILDER] only — CLI/S3 path skips it entirely, see GAP-4 |
| H3 | Pack excludes `workspace/`, `sessions/`, `.agentd/`, `.agentd-meta.json`, `.git`, `node_modules`, `clients/`, `__pycache__`, `*.pyc` | [BUILDER] pack |
| H4 | `[delivery] web = true` requires `[app]`; checked at pack AND at intake (fails closed) | [BUILDER]+[SERVER] |
| H5 | Bundle id: first creator to publish claims it FOREVER (atomic DynamoDB conditional put; 409 for everyone else) | [SERVER] |
| H6 | Version: PEP440, strictly newer than published, bump every shipped change | [SERVER] newer-check; missing version only a WARN — see GAP-6 |
| H7 | ≤64 MiB; artifact renamed server-side; index written under lock; entries signed with the CREATOR's key (root key signs only the roster); creator admission gate (202 parked until listed) | [SERVER] |
| H8 | Install verifies: roster signature vs pinned root key, replay refusal, sha256, publisher on live roster, zip-slip, compat, vendored plugins present; ownership re-stamped `installed` | [SERVER]/install — unpinned (BYOK) installs verify sha256 only |
| H9 | `requires_local` honesty: an agent needing exec/hot-loading must declare it (and vanish from hosted) | [TAUGHT] |

---

## THE GAPS (ranked, all confirmed with file:line in the four audit transcripts)

1. **The two creation paths disagree.** `agents.create` RPC (desktop UI "New Agent") writes NO
   `version` and skips `IDENTITY.md` when blank → born failing `NO_VERSION`+`NO_IDENTITY`.
   The builder's `create_agent` writes the full skeleton. Nothing reconciles them.
2. **SKILL.md teaches a falsehood + omits the fence.** `build-agent/SKILL.md:29` — "Reading is
   not restricted. Read anything you need." False on hosted since the tenant fence. The skill
   also never teaches: empty-workspace-per-user, no-exec-on-hosted, `[tools.fs]` write_roots
   grammar (which the builder itself uses), or ship-data-in-definition-dirs-never-workspace.
3. **agent-builder is `requires_local = true`** — there is NO agent building on hosted web at
   all today. Web users get only the bare `agents.create` RPC skeleton (see gap 1). "Builder
   per user on the web" requires either a fenced builder variant (drop create_tool/exec; keep
   create_agent/write/edit/validate — all fence-compatible) or desktop-build → publish → web.
4. **Validation is client-side and optional.** `publish_agent` skips validation when the
   validator is absent; `agentd bundle publish` (CLI/S3 operator path) validates NOTHING; the
   server only checks manifest shape + `[app]`-for-web. A broken agent is publishable.
5. **Origin/ownership softness at publish:** `_owned`/`_origin` default-permissive when the
   registry lacks the methods; `WEB_APP` origin missing from the refusal tuple; re-packing
   someone else's agent under a NEW id publishes fine (no content provenance).
6. **`NO_VERSION` is a WARN** → version-less agent packs as 1.0.0 and can never supersede itself.
7. **Heartbeat runs carry no account on hosted** (`_post_heartbeat` never binds one, unlike
   `_post_cron`) → hosted heartbeat resolves to shared/unaccounted state, workspace, memory.
8. **Cron bypasses the budget gate** (acknowledged in-code; re-resolve before hosted autonomy).
9. **Bundle contents unchecked:** native binaries, `.env` files, arbitrary data all ship;
   only the 64 MiB total cap; `pip`-sourced plugin deps install into the HOST interpreter
   with no allowlist/pinning/hash.
10. **`resources.sqlite` is not account-partitioned** (PRIMARY KEY (agent_id, rel_path), bare
    id) — one user's file metadata (names/sizes/summaries) can surface in another's manifest
    until reconcile overwrites. Memory got `memory_partition`; resources did not.
11. **Privilege propagation unflagged:** nothing stops/flags a built agent declaring
    `[tools.fs] write_roots = ["<agents_dir>"]` — builder-grade powers in a published agent.
12. **`docs/PROTOCOL.md` does not exist** though gateway.py and the SDK reference it; the
    stable-tier method list lives as two hand-maintained copies (code + SKILL.md).
13. `.agentd-meta.json` write failure is silent — an agent can exist with no ownership record
    and fall back to legacy layer-derived rules with no signal.
14. Reserved-id list is only `main` — nothing stops ids colliding with route segments
    (`apps`, `assets`, `new`); no length/leading-hyphen constraints.
15. Entitlement is decorative (recorded, never refused at install); no rate limit on
    `POST /registry/publish`; visitor-triggered web-app installs mutate the SHARED catalogue.

## Fix shapes for gaps 7–14 (all: one authority, one choke point, policy as data)

- **7 heartbeat/account**: store owning account on the heartbeat schedule (autonomy.sqlite
  already has the column), bind it before the run task like `_post_cron` does. Until then,
  fail closed: no heartbeats for account-owned agents on hosted.
- **8 cron/budget**: move `accounts.check_budget` to the ONE run-start choke point all run
  types pass through (chat/cron/heartbeat/channel) — one gate inherited forever, not four.
- **9 bundle contents**: admission scan at publish intake (it already unpacks there), driven
  by a policy TABLE: binary-extension denylist, secret-file patterns, per-file caps. pip
  sources: refuse in third-party bundles (vendored-only), or per-plugin venv — never the
  host interpreter.
- **10 resources partition**: key rows by `accounts.memory_partition(agent_id)` like memory
  does; same code both modes, different values. Standing rule: every daemon-global store
  keys on the partition, never bare agent_id.
- **11 privilege propagation**: publish ERROR when `write_roots` exceeds `<agent_dir>`; and
  the runtime backstop — `origin=installed` (ownership DATA) clamps declared write_roots to
  the agent's own folder at spec load.
- **12 PROTOCOL.md**: never hand-write; expose method tiers via RPC (extend capabilities.list)
  and generate the doc from the gateway constants. Self-describing, cannot drift.
- **13 silent meta**: ownership write is never best-effort — stamp FIRST, create fails if the
  stamp fails; one-time migration stamps legacy agents, then delete the guessing fallback.
- **14 reserved ids**: `RESERVED_IDS` set + shape rules (max length, starts alnum) inside
  `_valid_id` — the one validator every creation path funnels through; publish intake reuses
  the same domain function.

## What "fix the builder" means (the enforcement the user asked for)

- Rewrite SKILL.md's hosted model: the fence (B1–B6), no-exec (C3), `[tools.fs]` grammar (C2),
  empty-workspace (B6), version discipline (H6).
- Unify creation: make `agents.create` RPC emit the same skeleton as `create_agent`
  (version + IDENTITY.md at minimum) so every agent is born validator-clean.
- Move `validate_agent` server-side into publish intake (run layout/packageability/sandbox
  rules on the unpacked bundle) so the ERROR gate cannot be bypassed by any client.
- Promote `NO_VERSION` to ERROR at publish; add a validator rule flagging `write_roots`
  privilege propagation; add `WEB_APP` to the origin refusal tuple.
- Fix the two runtime leaks that affect built agents: bind the account on heartbeat runs;
  partition `resources.sqlite` like memory (`memory_partition` twin).
- Decide the web-builder story (gap 3) explicitly.
