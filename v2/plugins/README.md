# Plugins & Tool Config — Complete Guide (source of truth)

Everything about how plugins and their configuration work in agentd: how to **create**, **edit**,
**configure**, **enable/disable**, **override per agent**, and **understand** them end to end. If a
statement here disagrees with the code, the code wins and this doc is a bug — but it is kept in sync
with the implementation (file/line references throughout).

> **Mental model in one sentence:** a *plugin* is just a **namespace + a set of tools**; a *tool* is
> what an agent calls; and **all configuration lives on the tool**, keyed `plugins → tools → <knob>`,
> read from config only (never env).

---

## Table of contents

1. [What a plugin is](#1-what-a-plugin-is)
2. [Anatomy: the files in a plugin folder](#2-anatomy-the-files-in-a-plugin-folder)
3. [The manifest (`plugin.toml`) — every field](#3-the-manifest-plugintoml--every-field)
4. [The Tool contract](#4-the-tool-contract)
5. [`register()`, `PluginApi`, `PluginContext`](#5-register-pluginapi-plugincontext)
6. [Discovery & the four load gates](#6-discovery--the-four-load-gates)
7. [The config model: `plugins → tools → knobs`](#7-the-config-model-plugins--tools--knobs)
8. [The models layer (`tool_models.py`)](#8-the-models-layer-tool_modelspy)
9. [Per-agent overrides (`agent.toml`)](#9-per-agent-overrides-agenttoml)
10. [Enabling & disabling (four levels)](#10-enabling--disabling-four-levels)
11. [MCP plugins](#11-mcp-plugins)
12. [CLI / tooling (`list_plugins`)](#12-cli--tooling-list_plugins)
13. [Hot-loading & runtime authoring (`create_tool`)](#13-hot-loading--runtime-authoring-create_tool)
14. [Step-by-step: create a plugin](#14-step-by-step-create-a-plugin)
15. [Copy-paste templates](#15-copy-paste-templates)
16. [Editing & reading existing plugins](#16-editing--reading-existing-plugins)
17. [Reference tables](#17-reference-tables)
18. [Gotchas](#18-gotchas)

---

## 1. What a plugin is

A **plugin** is a self-contained unit that contributes **tools** (and optionally prompt sections,
skills, or an MCP server) to the one global **tool catalog** every agent draws from. Once a plugin's
tools land in the catalog they are ordinary guarded `Tool`s — indistinguishable from any other.

Two orthogonal axes classify a plugin:

- **Distribution** — *built-in* (shipped in `v2/plugins/`), *drop-in* (a folder you add), or *pip*
  (installed via an `agentd.plugins` entry point).
- **Kind** — `native` (Python tools we run in-process) or `mcp` (tools come from an external MCP
  server the plugin launches). Both normalize to the same `Tool` once in the catalog.

The core (`agentd/`) contains **no tool implementations** — every tool, including the built-ins,
flows through plugin discovery. Adding a tool never requires editing core.

---

## 2. Anatomy: the files in a plugin folder

A plugin lives in **one directory** under the plugins dir (default `v2/plugins/<id>/`). Minimum
is **two files** (manifest + a module with the tool and its `register`); the `register` can live in
the same `.py` as the tool, so practically it's often 2 files.

```
v2/plugins/weather/
├── plugin.toml          # REQUIRED — the manifest (declares the plugin without importing code)
├── weather_plugin.py    # the entry: def register(api, ctx)
├── weather_tool.py      # the Tool subclass(es)   (may be merged into weather_plugin.py)
├── skills/              # OPTIONAL — bundled SKILL.md files, auto-advertised
└── <assets>             # OPTIONAL — scripts/data declared in the manifest
```

Key mechanic: **the plugin folder is added to `sys.path`** at load time
([loader.py:23](../agentd/infrastructure/plugins/loader.py#L23)), so modules import each other by
bare name (`from weather_tool import WeatherTool`) — there is **no package**, no `__init__.py`, no
`agentd.` prefix for plugin-local modules.

---

## 3. The manifest (`plugin.toml`) — every field

Discovery reads `plugin.toml` **without importing your Python**, so a disabled or incompatible
plugin's heavy dependencies never load. Parsed by
[`load_manifest`](../agentd/infrastructure/plugins/manifest.py) into a `PluginManifest`.

```toml
id      = "weather"                     # REQUIRED. unique; MUST match the plugins.<id> config key
name    = "Weather"                     # human label (defaults to id)
kind    = "native"                      # REQUIRED. "native" | "mcp"
entry   = "weather_plugin:register"     # native: "<module>:<func>"; func defaults to "register"
enabled = true                          # author default (config plugins.<id>.enabled overrides)

# OPTIONAL — bundled assets (warn-if-missing, never fatal); resolve via ctx.resource("name")
scripts = ["helper.py"]
data    = ["prompts/system.txt"]

# OPTIONAL — the COMPATIBILITY gate. Plugin is SKIPPED unless ALL declared requirements hold.
[requires]
os   = ["windows", "linux", "darwin"]   # platform allowlist (any-of)
bins = ["ffmpeg"]                       # ALL must be on PATH
env  = ["SOME_API_KEY"]                 # ALL must be set

# OPTIONAL — only for kind = "mcp" (see §11)
[mcp]
command = ["uvx", "some-mcp", "--flag"] # stdio server; OR:
url     = "https://host/mcp"            # streamable-http server
env     = { KEY = "val" }
headers = { Authorization = "Bearer …" }
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | str | — (required) | Unique plugin id. The `plugins.<id>` config key and the enable gate key. |
| `name` | str | `id` | Human-readable name. |
| `kind` | str | — (required) | `native` (tools via `entry`) or `mcp` (tools from a server). |
| `entry` | str | `""` | `"module:func"`. Required for `native`; optional for `mcp` (prompt sections only). `func` defaults to `register`. |
| `enabled` | bool | `true` | Author default. Overridden by config `plugins.<id>.enabled`. |
| `scripts`, `data` | list[str] | `[]` | Bundled assets; missing ones warn (not fatal). |
| `[requires]` | table | `{}` | Compatibility gate: `os` (allowlist), `bins` (all on PATH), `env` (all set). |
| `[mcp]` | table | `{}` | MCP server spec: `command`/`url`/`env`/`headers`. |
| `root` | Path | (set by loader) | The plugin's directory. Added to `sys.path`; base for `ctx.resource()`. |

**No `plugin.toml` ⇒ the plugin is invisible.** Discovery only sees folders that contain a manifest.

---

## 4. The Tool contract

A tool implements the `Tool` ABC ([tool.py](../agentd/application/interfaces/tool.py)). Import it
with `from agentd.application.interfaces.tool import Tool, ToolResult`.

### Class attributes

| Attribute | Type | Default | Purpose |
|---|---|---|---|
| `name` | str | `""` | Unique tool id the model calls. **Required.** Duck-typed everywhere. |
| `description` | str | `""` | What the model reads to decide when to use it. Write it well. |
| `parameters` | dict | `{}` | **JSON Schema** for the tool's arguments (validated before `execute`). |
| `label` | str | `""` | Optional short human label for UIs. |
| `concurrency` | str | `"parallel"` | `"parallel"` (safe to run alongside siblings) or `"sequential"` (side effects — exec/edit/browser). |
| `plugin` | str | `""` | **Model-bearing only** — the plugin id, so config lookups find the tool. |
| `needs_model` | bool | `False` | **Model-bearing only** — `True` routes the tool into the model resolver; `False` = the resolver ignores it. |
| `default_model` | str | `""` | **Model-bearing only** — last-resort fallback model when nothing is configured. `""` = a text helper may inherit the brain. |

### `execute`

```python
async def execute(self, tool_call_id: str, params: dict,
                  abort: asyncio.Event, on_update: OnUpdate | None = None) -> ToolResult: ...
```
- `params` is already **validated** against `parameters` (a mismatch raises `ToolArgError` before you
  run — the loop turns that into an error result so the model self-corrects).
- `abort` is set when the run is cancelled — check it in long loops.
- `on_update(ToolResult)` streams incremental progress (optional).
- For **blocking/synchronous** work, do it off the event loop: `await asyncio.to_thread(self._run, params)`
  (the pattern most built-ins use).

### `ToolResult`

```python
ToolResult(content=[ContentBlock], details=Any, is_error=False)
ToolResult.text("done", details=None, is_error=False)   # convenience for the common case
```
- `content` is a list of content blocks (`TextContent`, `ImageContent`, …). Returning an
  `ImageContent` is how a tool hands the model a picture (a multimodal brain then *sees* it).
- `details` is structured data kept **out** of the model's view (raw payloads, etc.).
- `is_error=True` tells the loop the tool failed.

### Model-bearing tools

If the tool calls an LLM/VLM/diffusion/embedding model, declare the three attrs and resolve the model
through the layer (§8) — never hardcode a model id:

```python
from agentd.application.tool_models import resolve_tool_model

class VerifyFigureTool(Tool):
    name = "verify_figure"
    plugin = "vision"
    needs_model = True
    default_model = "gemini/gemini-2.5-flash"
    ...
    def _run(self, params):
        model = resolve_tool_model(self.config, self.plugin, self.name,
                                   per_call=params.get("model"), default=self.default_model)
        # ... call `model` ...
```
Backend **provider** (gemini vs replicate vs a search chain vs a browser engine) resolves the same
way via `resolve_tool_provider`; any other per-tool knob via `tool_config` (§8).

---

## 5. `register()`, `PluginApi`, `PluginContext`

The manifest's `entry` points to a `register(api, ctx)` function
([plugins.py](../agentd/application/interfaces/plugins.py)):

```python
def register(api, ctx):
    api.register_tool(WeatherTool(ctx.config))
    # optionally:
    api.register_prompt_section(lambda tools, agent, config: "## Weather\nUse `weather` for forecasts.")
```

### `PluginApi` — what a plugin may contribute

| Method | Contributes |
|---|---|
| `register_tool(tool)` | A tool (duck-typed on `.name` + `.execute`). Nameless tools are ignored with a warning. |
| `register_prompt_section(fn)` | A system-prompt block. `fn(tools, agent, config) -> str` is called each turn; return `""` to add nothing (e.g. gate on whether your tools are present). This is how a plugin *teaches* the model to use its tools without any core prompt code. |

### `PluginContext` (`ctx`) — read-only runtime handles

Every handle beyond `config`/`plugin_dir` is **optional (None when that subsystem is off)** — guard on
what you need; that's how `browser`/`computer` self-gate (return early when their handle is `None`).

| `ctx.<field>` | What it is |
|---|---|
| `config` | The app `Config` — your settings/keys. |
| `plugin_dir` | Your folder path. Prefer `ctx.resource("file")` to resolve bundled assets. |
| `browser` | Shared `BrowserManager` (Playwright/CDP session). |
| `computer` | Computer-use provider (screen/keyboard/mouse). |
| `task_store` | Durable cron/task ledger. |
| `memory_bank` | Long-term memory store. |
| `resource_manager` | Workspace resource index + CRUD. |
| `credential_store` | Saved-login vault (no plaintext secret ever reaches the model). |
| `connect_token_store` | Channel connect tokens. |
| `registry` | `AgentRegistry` — list/author agents at runtime. |
| `register_plugin_live` | `callable()` — hot-load a newly-written plugin into the live catalog (see §13). |
| `ctx.resource(name)` | Absolute path to a bundled file in your folder (relocation-proof). |

**Dependency rule (clean architecture):** a plugin depends **only** on `PluginApi` + the `Tool`
contract — never on the loader, catalog, or container. Handles are duck-typed, so adding a new one
never breaks an older plugin.

---

## 6. Discovery & the four load gates

At startup, [`discover_plugin_contributions`](../agentd/infrastructure/plugins/discovery.py) assembles
the catalog from three sources:

1. **Built-in dir** — `builtin_plugins_dir` (always `<V2_ROOT>/plugins`), scanned first and
   independently, so overriding the drop-in dir never drops the standard library.
2. **Drop-in dir** — `plugins_dir` (default `<V2_ROOT>/plugins`; override with `AGENTD_PLUGINS_DIR`).
   Each `<dir>/<id>/plugin.toml` is a candidate.
3. **pip entry points** — any installed distribution exposing the **`agentd.plugins`** entry-point
   group ([discovery.py:205](../agentd/infrastructure/plugins/discovery.py#L205)); `ep.name` is the id,
   `ep.value` is `"module:func"`.

Each candidate must pass **all four gates** ([`_passes_gates`](../agentd/infrastructure/plugins/discovery.py#L158)):

| Gate | Check | Source |
|---|---|---|
| **Installed** | a `plugin.toml` was found | a manifest exists |
| **Enabled** | `config.plugins[id].enabled` (or a bare bool), else the manifest's `enabled` | `_gate` |
| **Compatible** | `[requires]` os/bins/env all satisfied | `_compatible` |
| **Entitled** | the injected `EntitlementPolicy` allows it (open-source default: everything) | policy |

Passing plugins are loaded: [`load_plugin_entry`](../agentd/infrastructure/plugins/loader.py#L19)
puts the plugin's folder on `sys.path`, imports `entry` (`module:func`), and calls `func(api, ctx)`.
`native` → tools (+ sections); `mcp` → an `McpServerConfig` (tools appear when the gateway connects
the server) plus optional sections if it also has an `entry`. A plugin's `skills/` dir is advertised
like any skill folder. **All of this is zero-core-edit** — dropping a folder in is enough.

---

## 7. The config model: `plugins → tools → knobs`

All tool configuration lives in **one block** in `agentd.config.json`. It is the **control panel** for
every plugin and tool. Field: `Config.plugins` ([config.py](../agentd/config.py)).

```json
"plugins": {
  "<plugin>": {
    "enabled": true,                     // optional — toggle the whole plugin (omit => author default)
    "description": "...",                // optional — for humans; the agent NEVER sees it
    "tools": {
      "<tool>": {
        "enabled": true,                 // optional — toggle just this tool
        "description": "...",
        "provider": "...",               // backend/SDK/engine, where the tool has one
        "model": "provider/model",       // the AI model, where the tool calls one
        "<knob>": ...                    // ANY other per-tool knob (read via tool_config)
      }
    }
  }
}
```

Rules:

- **A plugin is pure grouping.** There is **no** `plugins.<plugin>.model` / `.provider` default — every
  knob lives on the tool. (An earlier "group knob" was removed for simplicity.)
- **`description` is documentary** — ignored at runtime by every resolver.
- **CONFIG-ONLY** — no environment variable feeds any model/provider/knob (env = keys, config = knobs).
- The block is **optional**: absent ⇒ tools run on their built-in defaults. Regenerate/extend it with
  `list_plugins --scaffold` (§12).

`config.plugins` serves **double duty**: model/provider/knob config **and** plugin/tool enablement.
`discovery._gate` reads `plugins[id].enabled` (a dict with an optional `enabled` key, or a bare bool);
omitted ⇒ the author default, so *configuring a plugin's models never silently flips its enablement*.

---

## 8. The models layer (`tool_models.py`)

[`agentd/application/tool_models.py`](../agentd/application/tool_models.py) is **the one modular place
any model is resolved** — brain and tools alike — and it reads **only from config** (+ the per-agent
`RunContext`), never env. It imports only `run_context`, so it's decoupled from infrastructure.

### Resolution precedence (first hit wins)

For **any tool knob** (`model`, `provider`, or a generic key):

```
per-call arg  >  agent.toml plugins[P].tools[T].<knob>  >  config plugins[P].tools[T].<knob>  >  built-in default
```
Agent overrides beat global config; there is **no plugin-level fallback**.

### Functions

| Function | Resolves | Notes |
|---|---|---|
| `resolve_tool_model(config, plugin, tool, per_call=None, default=None)` | a tool's model | empty value = "unset" (falls through). |
| `resolve_tool_provider(config, plugin, tool, per_call=None, default=None)` | a tool's backend provider | value may be a string **or a list** (e.g. web_search's fallback chain). |
| `tool_config(config, plugin, tool, key, default=None)` | any per-tool knob | **presence-based** — an explicit `false`/`""` is honored (for enable flags & arbitrary knobs). |
| `brain_model(config, agent_model=None)` | the reasoning (brain) model | `agent.toml model` → `config.model`. **Raises `ConfigMissingError`** if no config file was loaded or there's no model — LOUD, never a silent default. |

### Subsystem convenience resolvers

Some model users aren't `Tool` classes (the web-search grounder, the verify/safe-to-send judges, the
resource describers, computer-use, the memory/skills embedders). They resolve from the **same** plugins
map, each encoding its historical fallback chain in the `default`:

| Helper / call | Reads | Falls back to |
|---|---|---|
| `search_model(config)` | `plugins.web.tools.web_search.model` | the brain (`config.model`) |
| `verify_model(config)` | `plugins.verify.tools.verify.model` | search → brain |
| `safe_to_send_model(config)` | `plugins.safe_to_send.tools.safe_to_send.model` | verify → search → brain |
| `resource_summary_model(config)` | `plugins.resources.tools.summarize.model` | verify → brain |
| `resolve_tool_model(config,"resources","caption",…)` | `plugins.resources.tools.caption.model` | `RESOURCE_VISION_DEFAULT_MODEL` |
| `resolve_tool_model(config,"computer","computer",…)` | `plugins.computer.tools.computer.model` | `COMPUTER_DEFAULT_MODEL` |
| `resolve_tool_model(config,"memory","embed",…)` | `plugins.memory.tools.embed.model` | `MEMORY_EMBED_DEFAULT_MODEL` |
| `resolve_tool_model(config,"skills","relevance",…)` | `plugins.skills.tools.relevance.model` | `""` (feature off) |

### Behavioral (non-model) tool knobs

Tuning knobs that aren't a model or a provider (headless, step caps, timeouts, voice, …) live in the
**same** `plugins.<plugin>.tools.<tool>.<knob>` map and resolve via `tool_config` (presence-based, so an
explicit `false`/`0`/`""` is honored). They are **config-only** — there is deliberately **no `AGENTD_*`
env** for them (env = keys, config = knobs), and each is per-agent-overridable via `agent.toml`. The
built-in default is the last link, so every tool still works with an empty/absent plugins block. Two
convenience wrappers keep the `(plugin, tool)` pair out of the many call sites; any other tool calls
`tool_config` directly.

| Tool | Wrapper | Knobs (built-in default) |
|---|---|---|
| `browser` / `browser` | `browser_knob(config, key, default)` | `headless` (True), `persistent` (True), `cdp_url` (None — attach to a running Chrome), `downloads` (True), `channel` (`"chrome"` \| `""` for bundled Chromium), `stealth` (True), `cursor_scan` (True), `chrome_profile` (None), `console_buffer` (200), `action_timeout_ms` (12000), `agent_browser_command` (None → `["agent-browser","mcp"]`) |
| `computer` / `computer` | `computer_knob(config, key, default)` | `max_steps` (25), `send_max` (1440), `capture` (`"primary"` \| `"virtual"`), `pause` (0.15), `call_timeout_seconds` (120.0), `save_screenshots` (False; DEV only), `corral_to_primary` (True; Windows-only) |
| `shell` / `exec` | `tool_config(…, "shell", "exec", …)` | `timeout_sec` (1800; a per-call `timeout_sec` still wins) |
| `tts` / `tts` | `tool_config(…, "tts", "tts", …)` | `voice` (`"en-US-AndrewMultilingualNeural"`; a per-call `voice` still wins) |
| `resources` / `caption` | `tool_config(…, "resources", "caption", …)` | `timeout_seconds` (60.0) |

> Enablement flags (`computer_enabled`) and subsystem-wide caps (`resource_index_max_files`,
> `workspace_index_max_files`) are **not** per-tool knobs and stay as top-level `Config` fields.

### `config_path` & "config missing"

`load_config` records the loaded file in `Config.config_path` (`""` if none) and logs **`CONFIG
MISSING`** when absent. `brain_model` raises `ConfigMissingError` in that case; the gateway status shows
`(CONFIG MISSING)` instead of crashing. This is why *models come only from config* — with no config,
there is nothing to resolve, and the system says so loudly.

### Providers: single vs chain

- **imagegen** `provider`: single string — `gemini` | `fal` | `replicate`. The `model` is that
  backend's model/endpoint (a Gemini id for gemini; an `owner/name` slug for fal/replicate). Resolved
  in `generate_artwork_tool._route`.
- **web_search** `provider`: a **list** (a fallback chain tried in order), a single name, or `"auto"`
  (OpenClaw-style auto-detect from your keys). Each provider is skipped at query time if its key is
  missing (`available()`).
- **browser** `provider`: single string — `playwright` | `agent_browser`. Resolved in
  `config.resolve_browser_engine`.

---

## 9. Per-agent overrides (`agent.toml`)

Global `agentd.config.json` is the **default**; each agent's `agents/<id>/agent.toml` is an **override
layer stacked on top**. Flow: `file_registry` parses `[plugins]` → `AgentSpec.plugins` →
`RunContext.plugins` (set per turn in `agent_service`) → `current_plugins()`, and every resolver checks
the **agent layer first**.

```toml
# agents/figure-creator/agent.toml
name = "Figure Creator"

# brain (reasoning) model — overrides config.model for THIS agent
model = "gemini/gemini-3-flash-preview"

# a tool's model
[plugins.vision.tools.extract_anchors]
model = "gemini/gemini-3.1-pro-preview"

# a tool's provider + model (e.g. this agent renders on Replicate; others stay on Gemini)
[plugins.imagegen.tools.generate_artwork]
provider = "replicate"
model = "black-forest-labs/flux-1.1-pro"

# which tools this agent may see (allowlist); omit for "all tools"
[tools]
allow = ["web_search", "generate_artwork", "verify_figure"]
# deny = ["exec"]
```

Everything a tool reads (`model`, `provider`, any `tool_config` knob) is overridable this way. The
`agent.toml` value **wins over** the global config value. `[tools] allow/deny` controls **visibility**
(which tools the agent sees); config-level `enabled` is a **global** switch.

---

## 10. Enabling & disabling (four levels)

| Level | Where | Effect | Mechanism |
|---|---|---|---|
| **Plugin (global)** | `config.plugins.<id>.enabled = false` (or manifest `enabled = false`) | the whole plugin never loads | `discovery._gate` |
| **Tool (global)** | `config.plugins.<id>.tools.<tool>.enabled = false` | that one tool is dropped from the catalog | `apply_plugin_enablement` (container) |
| **Catalog allow/deny (global)** | `config.tools_enabled` / `config.tools_disabled` (name or trailing-`*` glob) | strict allowlist / denylist across the whole catalog | `apply_enablement` |
| **Per-agent visibility** | `agent.toml [tools] allow/deny` | which tools THIS agent sees | `select_tools` |

Tool-level enablement uses each tool's provenance tag `_plugin_id` (set by discovery) to find its
plugin, so it works for every plugin tool.

---

## 11. MCP plugins

For tools provided by an external **MCP server** rather than in-process Python, set `kind = "mcp"` and
declare the server under `[mcp]`. The plugin can *also* have an `entry` that contributes prompt sections
(but no tools — those come from the server). Real example — `plugins/google/plugin.toml`:

```toml
id    = "google"
name  = "Google Workspace"
kind  = "mcp"
entry = "google_plugin:register"     # contributes the "## Google accounts" prompt block only

[mcp]
command = ["uvx", "workspace-mcp", "--tools", "gmail", "drive", "calendar"]
env = { OAUTHLIB_INSECURE_TRANSPORT = "1" }
```

- `command = [...]` → a **stdio** server; `url = "..."` (+ optional `headers`) → a **streamable-http**
  server.
- The server's tools are discovered when the gateway connects and are **namespaced** `"<id>__<tool>"`.
- Keys/secrets the server needs are read from the process env (inherited from `.env`), not duplicated
  in the manifest.

---

## 12. CLI / tooling (`list_plugins`)

[`agentd/main/list_plugins.py`](../agentd/main/list_plugins.py) — inspect and scaffold the catalog. It
is generated from live tool metadata, so it never drifts.

```bash
python -m agentd.main.list_plugins            # human tree: every plugin, its tools, resolved models
python -m agentd.main.list_plugins --json     # machine-readable JSON (docs / tooling)
python -m agentd.main.list_plugins --scaffold # MERGE every discovered plugin+tool into
                                              # agentd.config.json's "plugins" block
```

`--scaffold` is **additive, idempotent, and order-preserving**: it adds any missing plugin/tool (with
its code `description`) while **keeping all existing knobs** (`enabled`/`provider`/`model`/custom keys).
Re-run it any time you add a tool. (DI-gated tools like `browser`/`computer` only load when their
subsystem is on, so their existing config entries are preserved even when not discovered in that run.)

---

## 13. Hot-loading & runtime authoring (`create_tool`)

An agent can **write and load a new plugin at runtime** with the `create_tool` tool (in the `authoring`
plugin, [create_tool_tool.py](authoring/create_tool_tool.py)). It writes
`<plugins_dir>/<id>/plugin.toml` + a Python module, then calls **`register_plugin_live`** (the reload
seam, injected via `ctx.register_plugin_live`) so the new tool joins the **live** catalog and is
callable immediately — **no restart**.

> ⚠️ **Danger:** this writes and runs new Python in-process (RCE by design). It is gated behind config
> and should only be enabled where you trust the agent.

The same `register_plugin_live` seam is what the container exposes for incremental hot-reload
([container.py](../agentd/main/container.py)) — newly discovered plugins are added without duplicating
already-loaded ones (`skip_ids`).

---

## 14. Step-by-step: create a plugin

1. **Make the folder** `v2/plugins/<id>/`.
2. **Write `plugin.toml`** with at least `id`, `name`, `kind = "native"`, `entry = "<module>:register"`
   (§3).
3. **Write the tool class** — subclass `Tool`, set `name` / `description` / `parameters`, implement
   `execute`. If it calls a model, add `plugin` / `needs_model` / `default_model` and resolve via
   `resolve_tool_model` (§4).
4. **Write `register(api, ctx)`** that does `api.register_tool(YourTool(ctx.config))` (§5). It can live
   in the same `.py` as the tool.
5. **Restart** (or use `create_tool` for live load). Discovery scans, gates, imports, registers — your
   tool is in the catalog (§6). No core edit.
6. **(Optional) Register in config** — run `python -m agentd.main.list_plugins --scaffold` to add it to
   `agentd.config.json` for description/enable/model/provider tuning (§12). Not required to function.
7. **An agent uses it automatically** — it's in the global catalog, so every agent sees it unless that
   agent's `agent.toml [tools] allow` excludes it (§9).

---

## 15. Copy-paste templates

### A. Minimal non-model tool (one file)

`v2/plugins/weather/plugin.toml`
```toml
id = "weather"
name = "Weather"
kind = "native"
entry = "weather_plugin:register"
```
`v2/plugins/weather/weather_plugin.py`
```python
from agentd.application.interfaces.tool import Tool, ToolResult

class WeatherTool(Tool):
    name = "weather"
    description = "Get the current weather for a city."
    parameters = {
        "type": "object", "required": ["city"],
        "properties": {"city": {"type": "string", "description": "City name."}},
    }
    def __init__(self, config):
        self.config = config
    async def execute(self, tool_call_id, params, abort, on_update=None):
        city = params["city"]
        return ToolResult.text(f"Sunny in {city}, 22°C")

def register(api, ctx):
    api.register_tool(WeatherTool(ctx.config))
```

### B. Model-bearing tool (extra bits only)

```python
from agentd.application.tool_models import resolve_tool_model, resolve_tool_provider

class SummarizeTool(Tool):
    name = "summarize"
    plugin = "textkit"
    needs_model = True
    default_model = "gemini/gemini-2.5-flash"
    description = "Summarize text with an LLM."
    parameters = {"type": "object", "required": ["text"],
                  "properties": {"text": {"type": "string"},
                                 "model": {"type": "string", "description": "Override the model."}}}
    def __init__(self, config):
        self.config = config
    async def execute(self, tool_call_id, params, abort, on_update=None):
        model = resolve_tool_model(self.config, self.plugin, self.name,
                                   per_call=params.get("model"), default=self.default_model)
        # ... call `model` via litellm/oneshot ...
        return ToolResult.text("…summary…")
```
Config after `--scaffold`:
```json
"textkit": { "tools": { "summarize": { "description": "…", "model": "gemini/gemini-2.5-flash" } } }
```

### C. MCP plugin (server-provided tools)

`v2/plugins/notion/plugin.toml`
```toml
id = "notion"
name = "Notion"
kind = "mcp"
[mcp]
command = ["npx", "-y", "@some/notion-mcp"]
env = { NOTION_TOKEN = "" }   # actually read from process env / .env
```
(No tool class needed — the server provides `notion__*` tools. Add an `entry` only if you want a prompt
section.)

---

## 16. Editing & reading existing plugins

- **See everything:** `python -m agentd.main.list_plugins` — the full plugin→tool tree with resolved
  models. `--json` for a machine view.
- **Find a tool's code:** it's under `v2/plugins/<plugin>/`; the tool's `name` is a class attr, its
  `plugin`/`needs_model` tell you if it's model-bearing.
- **Change a tool's model/provider:** edit `agentd.config.json` → `plugins.<plugin>.tools.<tool>` (or
  per-agent in `agent.toml`). No code change.
- **Change behavior/params:** edit the tool's `.py` (`parameters`, `execute`, `description`).
- **Turn a tool/plugin off:** set `enabled: false` at the right level (§10).
- **Keep presets handy** (JSON has no comments): park alternates in an inert `_`-prefixed key on the
  tool (e.g. `"_presets": {...}`) — resolvers ignore `_` keys and `--scaffold` preserves them.

---

## 17. Reference tables

**Config precedence (any tool knob):**
`per-call → agent.toml plugins[P].tools[T].<knob> → config plugins[P].tools[T].<knob> → built-in default`

**Brain model:** `agent.toml model → config.model` (config-only; raises `ConfigMissingError` if absent).

**Load gates (all four required):** installed · enabled · compatible · entitled.

**Where plugins are found:** `builtin_plugins_dir` (always `<V2_ROOT>/plugins`) · `plugins_dir`
(`AGENTD_PLUGINS_DIR`, default same) · pip entry-point group `agentd.plugins`.

**Key files:**

| Concern | File |
|---|---|
| Tool contract | `agentd/application/interfaces/tool.py` |
| Plugin/api contract | `agentd/application/interfaces/plugins.py` |
| Manifest parse | `agentd/infrastructure/plugins/manifest.py` |
| Discovery + gates | `agentd/infrastructure/plugins/discovery.py` |
| Entry loading (sys.path) | `agentd/infrastructure/plugins/loader.py` |
| Models layer | `agentd/application/tool_models.py` |
| Config + `load_config` | `agentd/config.py` |
| Per-agent parse | `agentd/infrastructure/agents/file_registry.py` |
| Enable/allow filters | `agentd/domain/agent.py` (`apply_enablement`, `apply_plugin_enablement`, `select_tools`) |
| Catalog CLI / scaffold | `agentd/main/list_plugins.py` |
| Runtime authoring | `plugins/authoring/create_tool_tool.py` |

---

## 18. Gotchas

- **No `plugin.toml` ⇒ invisible.** Writing a `.py` under `plugins/` is not enough; discovery only sees
  folders with a manifest.
- **Plugin-local imports are bare** (`from x import Y`), because the folder is on `sys.path`. Don't use
  `agentd.`-style paths for your own modules; do use them for the framework (`agentd.application...`).
- **Models come only from config** — no env var changes any model. Setting `AGENTD_MODEL` does nothing
  now; put the brain model in `config.json` (`"model": ...`) or per-agent `agent.toml`.
- **Missing config is loud** — `brain_model` raises `ConfigMissingError`; the status line shows
  `(CONFIG MISSING)`. Create `agentd.config.json` (or set `AGENTD_CONFIG`).
- **No plugin-level model/provider default** — put `model`/`provider` on the **tool**
  (`plugins.<p>.tools.<t>`), never on the plugin.
- **`provider` shape differs** — imagegen/browser take a single name; web_search takes a **list**
  (chain) or `"auto"`.
- **`description` in config is inert** — for humans only; it never reaches the model.
- **DI-gated tools** (browser/computer/simple_login) only register when their `ctx` handle exists, so
  they may be absent from `list_plugins` when their subsystem is off — their config entries are still
  preserved by `--scaffold`.
- **Enable vs visibility** — config `enabled` is global; `agent.toml [tools] allow/deny` is per-agent.
```
