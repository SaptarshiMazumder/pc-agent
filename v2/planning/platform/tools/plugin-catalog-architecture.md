# Tool Catalog & Plugins — Architecture Design

> **SUPERSEDED (historical design).** This was the pre-implementation blueprint. For how plugins &
> tool config ACTUALLY work now (create/edit/configure/override, the `plugins → tools → model` model,
> the models layer, enable gates, scaffolding), see the source of truth: **[../../../plugins/README.md](../../../plugins/README.md)**.
> Kept for design rationale only; where it disagrees with the code or that README, they win.

**Status:** design (no code yet — review before implementing).
**Goal:** one **tool catalog** that any agent draws from, assembled from four
interchangeable sources, with **per-tool ON/OFF in config** and **nothing coupled in
code**. A tool can be **internal** (shipped) or a **plugin** (downloaded on demand),
and **independently** be a **native** tool we build or an **MCP** server — all four
normalize to a guarded `Tool` and behave identically once in the catalog.

> Companion diagram: [`plugin-catalog-architecture.puml`](plugin-catalog-architecture.puml).
> Builds directly on [`mcp-architecture.md`](mcp-architecture.md) (reused for both
> internal-MCP and plugin-MCP) and the existing `build_tools` registry.

---

## 1. The one idea

**Everything normalizes to one flat `Tool` catalog; enable/disable is a config filter on
that catalog, not an `if` in code.** Packaging and implementation are two **orthogonal**
axes — a 2×2:

| | **native** (a `Tool` we build) | **MCP** (an external server/service) |
|---|---|---|
| **internal** (shipped in the app) | `read` · `write` · `exec` · `browser` · `web_*` | a bundled-by-default MCP |
| **plugin** (downloaded on demand) | a packaged Python tool (e.g. `video_edit`) | a 3rd-party / your own MCP server |

```
internal native ─┐
internal mcp    ─┤
plugin native   ─┼─▶ build_catalog ─▶ apply_enablement(config) ─▶ GuardedTool ─▶ [Tool]
plugin mcp      ─┘        (merge)         (uniform on/off)         (one wrap)      │
                                                                                   ▼
                                              per-agent select_tools(allow/deny) ─▶ agent
```

The loop only ever sees `Tool.execute(...)`. It cannot tell internal from plugin, nor
native from MCP. **"A plugin behaves exactly like an internal tool"** is *guaranteed by
normalization*, not by convention.

---

## 2. Layering (hexagonal — respects the existing import-linter contract)

`main > presentation > infrastructure > application > domain`

| Layer | New pieces | Why here |
|---|---|---|
| **domain** | `apply_enablement(tools, enabled, disabled)` — pure name/glob filter (sibling of `select_tools`) | IO-free policy; the global on/off rule |
| **application/interfaces** | `PluginApi` (Protocol: `register_tool`), `Plugin` (Protocol: `register`) | the **contracts** plugins implement (DIP) |
| **infrastructure/plugins/** | `manifest.py`, `discovery.py`, `loader.py`, `api.py` | parse `plugin.toml`, find + load plugins (the only place plugin IO lives) |
| **infrastructure/tools/mcp/** | *(reused as-is)* | both internal-MCP and plugin-MCP route through the existing `McpProvider` |
| **config.py** | `plugins: dict`, `tools_enabled: list`, `tools_disabled: list` | the toggles — JSON-configurable for free |
| **main/container.py** | `build_catalog(...)` — merge sources → enablement → guard | the **composition root** (only place concretes are named) |

Nothing in `application`/`domain` imports a plugin, the `mcp` SDK, or `importlib.metadata` —
those stay infrastructure details behind the interfaces (DIP).

---

## 3. Components (one responsibility each — SRP)

### domain/agent.py — the global on/off (pure, IO-free)
```text
def apply_enablement(tools, enabled, disabled) -> list[Tool]:
    # disabled wins; enabled=None|[] means "all". Matches by name or trailing-* glob,
    # exactly like select_tools' _matches. Used on the WHOLE catalog, uniformly.
```
Lives beside `select_tools` (same file, same duck-typing on `.name`) — so the **global**
filter (platform-wide) and the **per-agent** filter (allow/deny) share one matcher and
stay IO-free.

### application/interfaces/plugins.py — the contracts (DIP + ISP)
```text
class PluginApi(Protocol):              # what a plugin is handed to register itself
    def register_tool(self, tool: Tool) -> None: ...
    # (later, additive: register_channel, register_provider, register_gateway_method, ...)

class Plugin(Protocol):                 # what a native plugin module exposes
    def register(self, api: PluginApi, ctx: PluginContext) -> None: ...
```
A native plugin depends only on `PluginApi` + the existing `Tool` contract — never on the
loader, the catalog, or the container.

### infrastructure/plugins/manifest.py — parse + validate `plugin.toml` (SRP)
Pure-ish: read the manifest into a `PluginManifest` value object; reject unknown `kind`,
bad api-version. No importing of the plugin's code (that's the loader).

### infrastructure/plugins/discovery.py — find plugins (SRP)
Two roots (mirrors OpenClaw's `extensions/` + `node_modules/@openclaw/*`):
- **drop-in dir:** `~/.agentd/plugins/<id>/plugin.toml` (or `AGENTD_PLUGINS_DIR`),
- **pip entry points:** `importlib.metadata.entry_points(group="agentd.plugins")`.

Returns `PluginManifest`s for **enabled** plugins only (honors `config.plugins[id]` — a
disabled plugin is **never imported**, so its heavy deps never load).

### infrastructure/plugins/loader.py — turn a manifest into Tools (SRP, never-raises)
- `kind == "native"` → import `manifest.entry` (`pkg.module:register`), build a
  `CollectingPluginApi`, call `register(api, ctx)`, return `api.tools`.
- `kind == "mcp"` → hand `manifest.mcp` (command/url/env/headers) to the **existing
  `McpProvider`** → `discover()` → its `McpTool`s.
- **Graceful degradation:** a plugin that fails to import/connect is logged and skipped —
  others and the gateway are unaffected (same pattern as the browser/MCP factories).

### infrastructure/plugins/api.py — the registrar (SRP)
`CollectingPluginApi(PluginApi)` — `register_tool` appends to a list the loader reads back.
The seam where new capability types (channels, providers) get added later (OCP).

### main/container.py — `build_catalog` (the only place concretes meet)
Generalizes today's split (`build_tools` + the gateway's `_discover_mcp_tools`) into one
assembly. See §6.

---

## 4. The plugin manifest — `plugin.toml`

One format wraps **either** a native tool **or** an MCP server (the `kind` switch):

```toml
id          = "videoedit"
name        = "Video Editor"
kind        = "native"                      # "native" | "mcp"
api_version = "1"                           # compat with the PluginApi
enabled     = true                          # plugin-author default (config overrides)

# kind = "native":
entry       = "agentd_plugin_videoedit:register"   # a register(api, ctx) callable

# kind = "mcp":   (instead of `entry`)
# [mcp]
# command   = ["uvx", "some-video-mcp"]     # stdio server to launch, OR
# url       = "http://localhost:9000"       # hosted server to connect to
# env       = { FFMPEG_PATH = "..." }

# optional:
# config_schema = { ... }                   # JSON Schema for this plugin's own settings
# activation    = { on_startup = true, on_config_paths = ["videoedit"] }
```

`config_schema`/`activation` mirror OpenClaw's manifest and can be **deferred to a later
phase** — the MVP needs only `id`, `kind`, and `entry` **or** `[mcp]`.

---

## 5. Enablement — three independent layers (config, by name, decoupled)

All three are **data**, applied as **filters** — adding/removing a tool is a config line,
never a code change. **Crucially: app-level config is JSON (`agentd.config.json`),
per-agent config is TOML (`agent.toml`).**

| # | Layer | Where (file + key) | Effect |
|---|---|---|---|
| 1 | **Plugin load gate** | `agentd.config.json` → `"plugins": { "<id>": true\|false }` | off ⇒ plugin **never imported** → heavy deps never load |
| 2 | **Global tool on/off** | `agentd.config.json` → `"tools_disabled": [...]` and/or `"tools_enabled": [...]` (name/glob) | turns **any tool** on/off **platform-wide** — applied identically to all 4 sources |
| 3 | **Per-agent scope** | `agents/<id>/agent.toml` → `[tools] allow / deny` *(exists today)* | which tools **this agent** sees |

```jsonc
// agentd.config.json  (app-level — JSON; keys map onto Config fields via setattr)
{
  "channels": [ /* ... */ ],
  "plugins":        { "videoedit": true, "heavy_thing": false },   // layer 1
  "tools_disabled": ["computer", "video_edit_trim"]                // layer 2
  // or strict allowlist:  "tools_enabled": ["read","write","browser", ...]
}
```
```toml
# agents/sakana-sushi/agent.toml  (per-agent — TOML)
[tools]
deny = ["simple_login"]            # layer 3 (already implemented)
```

> **Why two formats:** `load_config` reads `agentd.config.json` and does
> `setattr(cfg, key, value)` for any key matching a `Config` field — so `plugins`,
> `tools_enabled`, `tools_disabled` become app config **for free** (same as `channels`,
> `tool_overrides`). Per-agent config has always been TOML in each agent's directory.
> Keep the two straight: **global → JSON; per-agent → TOML.**

**Dependency availability is orthogonal, NOT coupling.** A tool is *built* only if its
backend is wired (`browser` needs an engine; `memory` needs a bank). The toggles decide
*show/hide* among what's available. Today's scattered `if config.verify_tool` /
`if config.memory_enabled` checks in `build_tools` **collapse into layer 2** — built when
the dep exists, shown when enabled.

---

## 6. Container wiring (`build_catalog`)

```text
# main/container.py
def build_catalog(config, deps):                       # deps = browser/mcp/stores/...
    raw  = build_tools(config, ...)                     # internal native (today's registry)
    raw += discover_plugin_native(config)               # plugin native (dir + entry points)
    raw  = apply_enablement(raw, config.tools_enabled, config.tools_disabled)   # global on/off
    return [GuardedTool(t, resolve_policy(config, t)) for t in raw]

# async sources (connect at gateway startup, merged via the existing service.add_tools seam):
#   internal mcp  = build_mcp_provider(config).discover()      # configured servers
#   plugin  mcp   = discover_plugin_mcp(config) -> McpProvider.discover()
#   -> also passed through apply_enablement + GuardedTool before add_tools
```

- `build_tools` stays as the **internal-native** source — unchanged in spirit.
- MCP (internal + plugin) keeps its async-at-startup discovery (as today), merged through
  the existing `service.add_tools` path — just **also run through `apply_enablement`** so
  layer 2 is truly uniform.
- The engine, prompt, `select_tools`, and every existing tool are **untouched**.

---

## 7. Install / lifecycle UX

- **Install a plugin:** drop a package into `~/.agentd/plugins/<id>/`, **or**
  `pip install agentd-plugin-videoedit` (entry point auto-discovered). Restart (or a
  future hot-reload) → it appears in the catalog. A later `agentd plugin add/remove/list`
  CLI is sugar over this (OpenClaw's ClawHub / `npm install @openclaw/codex` analogue).
- **Heavy deps out of core (both kinds):** native plugin → deps in **its own** pyproject,
  `import` is **lazy** (inside `register`, only when activated); mcp plugin → deps in a
  **separate process**, never in agentd's venv. The core ships only the lightweight
  internal set. (Mirrors OpenClaw: a package per extension, `bundleRuntimeDependencies:
  false`, lazy import.)

---

## 8. SOLID — explicit mapping

| Principle | How this design honors it |
|---|---|
| **S**RP | manifest (parse) · discovery (find) · loader (instantiate) · api (collect) · `apply_enablement` (filter) · `build_catalog` (assemble) — each one reason to change |
| **O**CP | a new plugin (native or MCP) adds tools with **no change** to the catalog, loop, prompt, or existing tools — they extend through the `Tool` + `PluginApi` seams. New capability kinds (channels) add methods to `PluginApi` without touching tool plugins |
| **L**SP | a plugin tool **is** a `Tool` — substitutable everywhere (guard, validate, prompt, execute, enablement, select). MCP-plugin reuses `McpTool` |
| **I**SP | small interfaces — `PluginApi` (register_tool), `Plugin` (register), `Tool` (execute). No plugin depends on the loader or container |
| **D**IP | plugins → depend on `PluginApi`/`Tool` *interfaces*; loader/discovery (infra) hidden behind them; the **container** injects concretes. Interfaces in `application`, the filter in `domain` |

Plus the repo rule — **import-linter** stays green: interfaces in `application`, the pure
filter in `domain`, implementations in `infrastructure`, wiring in `main`.

---

## 9. Testing (no real plugins/network needed)

- **`apply_enablement`** (domain, pure): disabled removed; `enabled` allowlist; glob match;
  `disabled` wins over `enabled`; uniform across native + MCP-named tools.
- **discovery:** a temp `AGENTD_PLUGINS_DIR` with two `plugin.toml`s + a fake entry point →
  returns manifests; a plugin disabled in `config.plugins` is **absent** (assert its module
  was **never imported**).
- **loader (native):** a fake plugin whose `register` adds a `FakeTool` → tool in the
  catalog; an import error → skipped, others survive (never raises).
- **loader (mcp):** reuses the MCP `FakeTransport` → plugin-MCP tools appear, namespaced.
- **container:** all four sources present, each wrapped in `GuardedTool`; layer-2 toggle
  removes a tool platform-wide; per-agent `select_tools` still filters; `lint-imports` KEPT.

---

## 10. Phases

1. **Catalog + enablement (sync, no plugins yet).** Add `apply_enablement` (domain) +
   `tools_enabled`/`tools_disabled` + `plugins` config fields; fold `build_tools` into
   `build_catalog`; run MCP discovery through the same enablement. → *immediate value: turn
   **any existing tool** on/off from `agentd.config.json`, decoupled.*
2. **Native plugins.** `PluginApi`/`Plugin`, `plugin.toml`, discovery (dir + entry points),
   loader, lazy import, layer-1 load gate. → *`pip install` or drop-in a Python tool; it
   joins the catalog.*
3. **Plugin-MCP.** Route `kind = "mcp"` through the existing `McpProvider`. → *downloadable
   MCP-server plugins.*
4. **Polish.** `agentd plugin add/remove/list` CLI; manifest `activation` rules; expose the
   catalog as a `tools.list` gateway surface (clients see all installed tools + which agents
   can use them).

> Phase 1 alone delivers the headline ask — *every tool toggled on/off in config, nothing
> coupled* — for the tools that exist **today**, before any plugin machinery lands.
