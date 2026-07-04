# agentd Product & Distribution Plan — CLI, Desktop, SKUs, Marketplace

**Status:** design (no code yet — review before implementing).
**Goal:** ship agentd as a product, in stages: (1) a one-command installable CLI, (2) a
Claude-Desktop-style desktop client, (3) two sale shapes — a **core** app and **specialized
standalone** apps (e.g. Figure Creator) — built from the same codebase, and (4) an in-app
**marketplace** where a core install can download additional agents.
**Scope guard:** agentd core stays as-is. Everything here is packaging, lifecycle, clients,
and distribution *around* the existing daemon. Pricing/billing is deferred but its seams are
named (§8), consistent with [plugin-distribution-architecture.md](../tools/plugin-distribution-architecture.md) §7.

> Companion docs: [plugin-distribution-architecture.md](../tools/plugin-distribution-architecture.md)
> (tiers: core / bundled / addon; the Provisioned gate) and
> [plugin-catalog-architecture.md](../tools/plugin-catalog-architecture.md) (historical; catalog model).
> This doc is the product/distribution umbrella above both.

---

## 0. The vision, restated in product terms

| # | Ask | Product term used below |
|---|---|---|
| 1 | `jarvis` / `agentd` in one command | **Installable CLI** (Phase 1) |
| 2 | Desktop client like Claude Desktop | **Desktop shell** (Phase 2) |
| 3 | Sell core + sell specialized agents standalone | **SKUs / build flavors** (Phase 3) |
| 4 | "Install" agents from within the app | **Marketplace + agent bundles** (Phase 4) |
| 5 | One client, many agents (Creative Cloud / Riot Client) | the sum of 2+3+4 |

The single most important architectural fact: **this is already a client–server product.**
`python -m agentd` boots a WebSocket gateway (`presentation/gateway.py`, `chat.send` /
`chat.event` frames) and the terminal REPL (`clients/terminal`) is a *pure WS client*. The
desktop app is therefore **not a rewrite** — it is a second client speaking the same protocol,
plus a process supervisor. Everything in this plan leans on that.

---

## 1. Current state — what's load-bearing, what's missing

### Already built and directly reusable

| Capability | Where | Why it matters here |
|---|---|---|
| Gateway + JSON protocol (runs, sessions, agents, crons, notifications, streaming events) | `agentd/presentation/gateway.py`, `protocol.py` | Desktop + terminal + store UI are all "just clients" |
| Plugin discovery: drop-in dir (`plugins/<id>/plugin.toml`) **and pip entry points** (`group="agentd.plugins"`) | `infrastructure/plugins/discovery.py` | Marketplace installs = drop a folder or `pip install` — **no new loader needed** |
| Manifest-before-import + 4 load gates (installed / enabled / compatible / agent-scoped) | `plugins/manifest.py`, config `plugins{}` gates | Disabled paid plugin's deps never even import |
| **EntitlementPolicy seam** (AllowAll default, swap at composition root) | `infrastructure/plugins/entitlement.py` | The licensing hook already exists, unbuilt by design |
| Incremental hot-reload of newly added plugins (`skip_ids` set) | `discovery.discover_plugin_contributions` | "Install from store, usable without restart" |
| Agents are directories (`agent.toml` + IDENTITY.md + skills/ + templates/) | `infrastructure/agents/file_registry.py` | An installable agent = **a folder in a zip**. This is the whole marketplace unit |
| Per-agent model/tool config that beats global config | `application/tool_models.py`, agent.toml `[plugins.*]` | A shipped agent bundle carries its own engine wiring (figure-creator already proves it) |
| Terminal client with pickers, streaming markdown | `clients/terminal` | Ships as the CLI's `chat` mode on day one |

### Missing (the actual work)

1. **Packaging** — no `pyproject.toml`; runs only from the repo (`python -m agentd`), deps via
   `requirements.txt`; built-ins pinned to `<V2_ROOT>/plugins`.
2. **User-level install layout** — config/agents/plugins/state under `~/.agentd` exists partially
   (`state_dir`) but there's no first-run bootstrap, no canonical layout, no onboarding.
3. **Daemon lifecycle** — nothing auto-starts/stops/health-checks the gateway; no port/PID
   discovery file; no local auth token on the WS.
4. **Desktop client** — none.
5. **Bundle format + registry + installer flow** — none (design exists in the tiers doc).
6. **Licensing/entitlement implementation** — seam only.

---

## 2. Target architecture (one picture)

```
                        ┌────────────────────────────────────────────────┐
                        │                REGISTRY (cloud)                │
                        │  index.json + signed bundles (.agentpkg / pip) │
                        │  v0: static CDN/GitHub · v1: accounts+licenses │
                        └────────────────────────┬───────────────────────┘
                                                 │ https (download / verify)
┌─────────────── one machine ─────────────────────────────────────────────────┐
│                                                 ▼                           │
│  ~/.agentd/                          ┌──────────────────┐                   │
│    config.json                       │  agentd daemon    │  ← UNCHANGED core│
│    gateway.json (port/pid/token)     │  (gateway + loop  │                  │
│    agents/<id>/       ◄── installs ──│   + tool catalog) │                  │
│    plugins/<id>/      ◄── installs   └───┬──────────┬────┘                  │
│    state/  licenses/                     │ ws://localhost (token)           │
│                                          │          │                       │
│                              ┌───────────┴──┐   ┌───┴─────────────────┐     │
│                              │ terminal CLI │   │ DESKTOP SHELL       │     │
│                              │ agentd chat  │   │ chat UI · agent     │     │
│                              └──────────────┘   │ switcher · STORE UI │     │
│                                                 │ + daemon supervisor │     │
│                                                 └─────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────────┘
```

Three invariants:

- **The daemon is the only brain.** Clients never run tools or talk to LLMs; they render events
  and send requests. (Already true; keep it true — it's what makes SKUs/flavors cheap.)
- **The install unit is a bundle** (an agent folder + its plugin deps + metadata). Nothing in
  core knows about "products"; a SKU is just *which bundles and plugins a given installer lays
  down* plus a branding profile — exactly the "package profile" from the tiers doc.
- **One codebase, many flavors.** Core app and Figure Creator Studio differ only in a build-time
  `distribution.toml` (name, icon, preinstalled bundles, provisioned plugin set, store on/off).

---

## 3. Phase 1 — Installable CLI (`agentd` / `jarvis`)

**Outcome:** `pipx install agentd` (or `uv tool install agentd`) → type `agentd` → onboarding on
first run → chatting with `main`. Repo clones keep working unchanged.

### 3.1 Package it

- Add **`v2/pyproject.toml`** — `[project] name = "agentd"` (PyPI availability TBD; fallbacks:
  `agentd-ai`, `jarvis-agentd`), deps lifted from `requirements.txt` with the heavy optionals as
  extras (`[computer]`, `[mcp]`, `[office]`), and:
  ```toml
  [project.scripts]
  agentd = "agentd.cli:main"
  jarvis = "agentd.cli:main"     # same binary, both names — decide branding later, keep both now
  ```
- **Move `clients/terminal` into the package** (`agentd/clients/terminal/`) so the REPL ships in
  the wheel. Leave a thin import shim at `v2/clients/terminal` so dev workflows don't break.
- **Built-in plugins into the wheel.** Today discovery hard-fixes built-ins to `<V2_ROOT>/plugins`.
  Change the "built-ins root" resolution to: packaged dir (`agentd/_builtin_plugins/`, real files
  via wheel install — native plugin `root` on `sys.path` keeps working) → else `<V2_ROOT>/plugins`
  when running from a repo checkout. Which built-ins go in the *core* wheel vs stay repo-only is
  the **core plugin set** decision (§9 D4); everything else becomes bundled/addon per the tiers doc.
- **Starter agent**: `main` is already synthesized by `file_registry` when absent — the wheel needs
  no agents at all. Named agents (figure-creator etc.) explicitly do **not** ship in the core wheel;
  they are bundles (Phase 3/4).

### 3.2 Canonical user layout + first run

```
~/.agentd/
  config.json          # was agentd.config.json at repo root; repo file still wins in dev checkouts
  gateway.json         # written by the daemon on bind: {port, pid, token, version}
  agents/<id>/         # user + installed agents (agents_dir default points here when packaged)
  plugins/<id>/        # drop-in + marketplace plugins (AGENTD_PLUGINS_DIR default when packaged)
  state/               # sessions, memory, tasks (current state_dir content)
  licenses/            # Phase 5
  logs/
```

First run (`agentd` with no config): tiny interactive onboarding — pick a provider, paste an API
key (stored via the existing credentials infra), write `config.json`, verify with a 1-token ping.
BYOK is the day-one model (§9 D2).

### 3.3 CLI surface (thin argparse/typer over what exists)

```
agentd                    # ensure daemon running (spawn if needed), attach chat REPL — the openclaw feel
agentd serve              # foreground daemon (current python -m agentd)
agentd chat [--agent id]  # attach REPL only
agentd status | stop      # via gateway.json (pid/port/token)
agentd agents list|new    # wraps the existing registry/gateway ops
agentd plugins list       # wraps main/list_plugins.py
agentd install <id|file|url>   # Phase 4 — bundle installer (CLI first, store UI later)
agentd doctor             # check keys, playwright browsers, ffmpeg, plantuml, optional deps
```

### 3.4 Daemon lifecycle + local auth (small, do it now)

- Daemon writes `gateway.json` on bind, removes on clean exit; clients read it, health-check the
  pid/port, and **auto-spawn** the daemon (detached) when absent. One daemon per user; second
  `serve` exits politely pointing at the live one.
- **Add a bearer token to the WS handshake** (random per-install, stored in `gateway.json`,
  0600). Localhost WS is reachable by any local process *and by any webpage via JS* — close that
  now while it's a 20-line change; the desktop client will need it anyway.

**Exit criteria:** fresh Windows machine, `pipx install`, `agentd`, onboarding, chat with main,
`agentd doctor` green (or telling you exactly what to install). Repo `python -m agentd` unchanged.

---

## 4. Phase 2 — Desktop shell

**Outcome:** an installable desktop app that looks/feels like Claude Desktop: sidebar of
sessions/agents, streaming chat with markdown + tool-activity blocks, notifications — and (later)
the store tab. It supervises the daemon; the user never sees Python.

### 4.1 Stack — recommendation: **Electron + React + electron-builder/updater**

| | Electron (recommended) | Tauri v2 (alternative) |
|---|---|---|
| Fit | Claude Desktop is Electron; mature sidecar + autoupdate + tray story | 10× smaller shell, lower RAM |
| Risk | App size (~100 MB shell) — dwarfed by the Python runtime anyway | Rust onboarding cost; webview quirks across Win versions |
| Verdict | **Ship v1 on Electron** | Revisit if shell footprint ever matters |

The renderer speaks the **existing WS protocol** — the terminal client is the reference
implementation of every frame type (streamed markdown, ⏺ tool call / ⎿ result blocks, pickers →
become real UI). No protocol rewrite.

### 4.2 The hard part: bundling the Python daemon

Recommendation: **embed a `python-build-standalone` runtime + a prebuilt venv of the agentd
wheel**, assembled by the installer — *not* PyInstaller. Two reasons:

1. PyInstaller with this dep tree (playwright, litellm, lxml, PIL, google-genai…) is a
   hidden-imports whack-a-mole and breaks on every dep bump.
2. **Strategic:** marketplace plugins can arrive as pip packages (`agentd.plugins` entry points
   — already supported by discovery). A frozen binary cannot `pip install` into itself; an
   embedded CPython + venv can. This one choice keeps the pip half of the plugin story alive.

Layout: `<app>/resources/runtime/` (CPython) + `<app>/resources/agentd-env/` (venv). The shell
spawns `agentd serve` from there, health-checks, restarts on crash, and pipes logs to
`~/.agentd/logs/`. Auto-update: electron-updater for the shell; the daemon env updates as a
versioned artifact the shell downloads and swaps (keep N−1 for rollback).

### 4.3 Protocol additions (small, additive)

- `client.hello` version/capability exchange (protocol versioning starts here, before two client
  types exist in the wild).
- File attachments in `chat.send` (paths for local clients; upload RPC later for remote).
- `marketplace.*` RPCs (Phase 4) and install-progress events.
- Branding/metadata on agent list entries (display name, icon, description) so the switcher looks
  like a product, not a folder listing.

**Exit criteria:** installer on a clean Windows box → app opens → onboarding → chat with main
with streaming + tool blocks; daemon lifecycle invisible; auto-update works shell-side.

---

## 5. Phase 3 — SKUs: core app + specialized standalones

**Outcome:** two sale shapes from one codebase — **agentd Core** (main agent + core plugin set)
and e.g. **Figure Creator Studio** (core + figure-creator preinstalled + branding). Adobe CC /
Riot Client model: the platform is the same client underneath.

### 5.1 The unit: an **agent bundle** (`.agentpkg`)

A zip with a manifest — the marketplace artifact *and* the SKU pre-install artifact are the same
thing:

```
figure-creator-1.0.0.agentpkg
├─ bundle.toml            # id, name, version, description, icon,
│                         #   agentd_compat = ">=0.3,<0.5"
│                         #   plugins = ["figures","imagegen","vision","vectorize","figexport"]
│                         #   (each: id + version-range + source: vendored | pip | registry)
│                         #   entitlement = "figure-creator-pro"   # Phase 5; absent = free
├─ agent/                 # → unpacked to ~/.agentd/agents/<id>/  (agent.toml, IDENTITY.md,
│                         #    skills/, templates/ — figure-creator already IS this, unchanged)
└─ plugins/               # optional vendored drop-ins → ~/.agentd/plugins/<id>/
```

Figure-creator needs **zero changes** to become a bundle — `agent.toml` already carries its whole
engine wiring (models, template default, tool knobs) portably. That's the proof the unit is right.

### 5.2 Build flavors, not forks

One `distribution.toml` baked into each installer at build time:

```toml
[product]
name = "Figure Creator Studio"        # window title, installer name, icon set
default_agent = "figure-creator"
preinstalled_bundles = ["figure-creator"]
[provisioning]                         # = the "package profile" from the tiers doc, verbatim
plugins = ["core_fs","shell","web","skills","memory","figures","imagegen","vision","vectorize","figexport"]
[store]
enabled = true                         # a standalone SKU can still upsell other agents
```

The desktop shell reads it for branding + default agent; the daemon composition root reads the
provisioning list as its **Provisioned** gate input (the tiers doc's Gate 2 — this is where that
design gets implemented). Core SKU = same file with `name="agentd"`, no preinstalled bundles,
core provisioning. CI builds N installers from N toml files.

---

## 6. Phase 4 — Marketplace

**Outcome:** from a core install, open Store → hit **Install** on Figure Creator → it appears in
the agent switcher, tools live, no restart.

### 6.1 Registry — start embarrassingly simple

**v0 (static):** an `index.json` on a CDN / GitHub releases: per bundle → id, name, version,
description, icon URL, `agentd_compat`, download URL, `sha256`, price tier (informational until
Phase 5). First-party bundles only. This is enough for everything in this phase.

**v1 (service):** accounts, purchase records, per-user entitlement issuance, download auth,
versioned channels (stable/beta), publisher pipeline. Build only when payments start.

### 6.2 Install flow (one implementation, two frontends)

`agentd install figure-creator` (CLI, ships first) and the Store UI (desktop) call the same
gateway RPC `marketplace.install`:

1. Fetch index → resolve version against `agentd_compat` (refuse with a clear "update agentd"
   message on mismatch).
2. Download → verify `sha256` (v0) / signature (v1, §7).
3. Unpack `agent/` → `~/.agentd/agents/<id>/`; vendored `plugins/` → `~/.agentd/plugins/<id>/`;
   `source="pip"` plugin deps → `pip install` into the daemon's env (why §4.2 chose a real venv).
4. Add the bundle's plugins to the install's provisioning set; record
   `~/.agentd/state/installed_bundles.json` (id, version — this is what "check for updates" reads).
5. Hot-reload: plugins via the existing `skip_ids` incremental discovery; agents need a
   `FileAgentRegistry.refresh()` (it currently discovers once at construction — the one small
   core-adjacent change in this phase) → emit `marketplace.installed` event → client refreshes
   the switcher.

Uninstall = reverse (keep the agent's memory/session state under `state/` unless the user opts to
purge). Update = install new version over old after compat check.

### 6.3 Trust

v0 is first-party-only, so hash-pinning via the index is acceptable. **Before any third-party
bundle ever lands:** ed25519-sign bundles, pin the publisher key in the app, verify before
unpack. An agent bundle is *code that runs with the user's tools* — treat install like installing
software, because it is. Third-party publisher sandboxing/review is explicitly out of scope here;
gate it behind a separate design doc when the time comes.

---

## 7. Phase 5 (deferred, seams now) — licensing, accounts, payments

- **Entitlement:** implement `LicenseFileEntitlement` behind the *existing*
  `EntitlementPolicy` interface: reads signed license files from `~/.agentd/licenses/`
  (claims: sku/bundle id, plugin ids, expiry, machine or account binding), verifies against a
  pinned public key, entitles matching plugins. Free bundles need no license — AllowAll semantics
  for unmarked plugins. Offline-friendly (signed file, periodic revalidation when online).
- **Payments:** Stripe checkout in the store → registry v1 issues the license → client drops it
  in `licenses/` → hot entitlement refresh. No payment code in the client beyond opening checkout.
- **Accounts:** only needed when payments/licensing arrive; onboarding stays account-free until then.

Nothing in Phases 1–4 blocks on any of this — which is the point of the seam.

---

## 8. Cross-cutting concerns

- **Platform support:** Windows-first (current reality: Proactor loop, dev machine), but keep
  POSIX green in CI from Phase 1 — macOS is the natural second platform for a desktop product.
- **Heavy runtime deps** (playwright browsers ~300 MB, ffmpeg, plantuml/java): never in the
  installer. `agentd doctor` + on-demand fetch on first use of the tool that needs them; bundles
  declare needs via the existing manifest `requires` gate so a missing binary degrades the tool,
  not the app.
- **Versioning:** semver the daemon; protocol version in `client.hello`; `agentd_compat` ranges
  in bundles. Three artifacts move independently (shell, daemon env, bundles) — each has its own
  update channel, all user-visible state stays in `~/.agentd` so any of them can roll back.
- **Repo shape:** packaging from `v2/` inside pc-agent works for Phase 1 (pyproject at `v2/`).
  Expect to promote v2 to its own repo (or repo root) around Phase 2 when CI builds installers —
  decide then, not now.
- **Telemetry/crash reporting:** opt-in only, decide before public distribution; at minimum
  daemon logs + a "report issue" bundler in the shell.

---

## 9. Decisions needed (recommendations inline)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Desktop stack | **Electron** now; Tauri only if footprint becomes a selling point |
| D2 | Keys | **BYOK at launch**; managed/proxied billing is a Phase-5+ business decision |
| D3 | Command name | Register **both** `agentd` and `jarvis` scripts now; brand later, zero cost |
| D4 | Core plugin set | Propose: `core_fs, shell, web, skills, memory, planning, resources, authoring` — everything else bundled/addon. Needs your sign-off |
| D5 | Daemon bundling | **Embedded CPython + venv** (keeps pip-plugin path alive) over PyInstaller |
| D6 | PyPI publish vs private index for Phase 1 | Public PyPI if the name is free; else private index + `pipx install --index-url` until launch |

---

## 10. Execution order (each milestone independently shippable)

| M | Deliverable | Builds on | Rough size |
|---|---|---|---|
| **M1** | `pyproject.toml`, CLI surface, `~/.agentd` layout, built-ins-in-wheel, first-run onboarding | — | S–M |
| **M2** | Daemon lifecycle (`gateway.json`, auto-spawn, single-instance) + WS auth token + `client.hello` | M1 | S |
| **M3** | Electron shell MVP: supervisor + chat UI at terminal-client parity, packaged installer w/ embedded runtime | M2 | L (the big one) |
| **M4** | Bundle format + `agentd install` from static registry v0 + registry `refresh()` + hot-reload wiring | M1 (not M3!) | M |
| **M5** | Store UI in the shell (`marketplace.*` RPCs over M4's flow) | M3+M4 | M |
| **M6** | `distribution.toml` flavors + Provisioned gate → **Figure Creator Studio** installer built in CI | M3+M4 | M |
| **M7** | `LicenseFileEntitlement` + signed bundles + registry v1 + payments | M5+M6 | L, later |

Note the parallel track: **M4 (bundles/CLI-install) doesn't wait for the desktop app** — the
marketplace mechanics mature on the CLI while M3 is in flight, and M5 is then just UI over a
proven flow.
