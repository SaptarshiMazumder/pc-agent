# Plugin Migration Plan — toward the OpenClaw uniform model

**Status:** plan (review before building). **Goal:** converge agentd onto OpenClaw's uniform
model — **one home for all tool/capability code (`plugins/`), each bundling tools + skills +
card; the core becomes framework-only; MCP stays a central connection registry** — while keeping
the architecture clean (import-linter green), scalable (gated/budgeted skills), and **breaking
nothing** (full suite + lint pass at the end of every phase).

> We keep the name **`plugins/`** (OpenClaw calls the same idea `extensions/` — wherever this doc
> says "OpenClaw extensions" it means *their* folder; *ours* is `plugins/`).
> Companion docs: [plugin-catalog-architecture.md](plugin-catalog-architecture.md) ·
> [plugin-distribution-architecture.md](plugin-distribution-architecture.md) ·
> [mcp-architecture.md](mcp-architecture.md).

---

## 0. Already built (do NOT redo)

> **⚠️ Card system REMOVED (later decision).** The `.md` "card" mechanism below (`tools/<name>.md`,
> plugin `CARD.md`, per-tool plugin cards, `infrastructure/cards.py`, `tools_dir`, `tool_cards`) was
> **deleted** in favor of the OpenClaw model: **a tool self-describes via its own `description`**
> (→ tool schema + the `## Tooling` summary's first line), and any strong/shared guidance is a
> **plugin prompt section** (`register_prompt_section`, like `google_section`). The old card BODIES
> (`## Planning`, `## Verify`) now live as prompt sections in the `planning` / `verify` bundles; the
> google guidance folded into `google_section`. So ignore "card" mentions below — they're historical.

| Capability | State |
|---|---|
| Plugin catalog + native plugin discovery (dir + entry-points) + per-plugin load gate | ✅ done |
| Plugin-MCP routing (`kind="mcp"`) + Google moved to `plugins/google/` | ✅ done |
| Global on/off enablement (`tools_enabled`/`tools_disabled`, `apply_enablement`) | ✅ done |
| Tool **cards** (`tools/<name>.md`: summary + instruction block) + generic prompt assembly (no hardcoded `TOOL_SUMMARIES` / `if name==…`) | ✅ done |
| Cards/plugins **scripts/data** declaration + `ctx.resource()` resolver | ✅ done |
| `tools.list` (basic) + terminal `/tools` | ✅ done |
| Skill-invocation logging (`skill invoked: …`) | ✅ done |
| **Phase A** — plugin `CARD.md` + plugin-bundled `skills/` merged into `resolve_skills` | ✅ done |
| **Phase B** — skill gating (`requires_bins/env/config`) + prompt budget (cap → compact → truncate) | ✅ done |
| **Phase C** — MCP hot-add (`mcp.add/list/remove` + terminal `/mcp`, live connect + persist) | ✅ done |
| **Phase D** — source-tagged `tools.list` (`internal`/`plugin:<id>`/`mcp:<server>`) + card summaries | ✅ done |
| **Phase E** — plugin dependency injection (`PluginContext` injected handles) | ✅ done |
| **Phase F** — ALL internal tools → built-in plugin bundles (plugins/); Tool contract → application/interfaces | ✅ done |
| **Phase G** — `build_tools` removed; catalog = pure discovery; import-linter `agentd` ⊥ `plugins` | ✅ done |
| **Phase H** — plugin 4-gate (compatible + entitlement) + relevance-filtered skills | ✅ done |

The plan below builds **on top of these** — it does not touch them except where noted.

---

## 1. Target end-state (the architecture)

```
agentd/                   ← CORE = framework ONLY (no tool implementations)
   infrastructure/tools/     Tool contract · build/registry · guard · loop · MCP client
   …engine · gateway · prompt assembly · skill loader …
plugins/<name>/           ← ONE home for ALL tool/capability code (built-in AND downloaded)
   plugin.toml               kind · entry · [mcp] · declared skills · scripts/data
   <code>.py                 tools (native) and/or a dynamic prompt section
   CARD.md                   summary + static instruction block   (uniform with internal cards)
   skills/<name>/SKILL.md     bundled playbooks                    (NEW: plugins carry skills)
   scripts/ · data/          bundled assets (declared, resolved via ctx.resource)
skills/                   ← top-level COMPLEX / shared skills (kept)
config.mcp_servers        ← bare MCP CONNECTIONS (central registry, via /mcp add)
        ▲
   tools.list / /tools / /mcp list   ← ONE central runtime VIEW (source-tagged + card summary)
```

**Three clean categories, no overlap:**
- **code** → `plugins/` (one folder, built-in + downloaded), each a self-contained unit.
- **bare MCP connections** → `config.mcp_servers` (central registry; `/mcp add`). *Not* code → not a plugin folder. A *curated* MCP that ships prompt/skills is a plugin declaring `kind="mcp"`.
- **complex/shared skills** → top-level `skills/`. Per-plugin skills ship in the plugin.

---

## 2. Principles (hold at every phase)

1. **Additive-first, risky-move-last.** Build the machinery on today's structure; move core code only once the target format + DI are proven.
2. **Green gate per phase.** Each phase ends with `pytest tests/` + `lint-imports` passing. No phase half-migrates a thing.
3. **One mechanism, not three.** End state: built-in and downloaded tools load through the *same* path; the only "internal vs external" difference becomes *where the folder ships*, not *how it's wired*.
4. **MCP is a connection, not code.** It never becomes a plugin folder (matches OpenClaw).
5. **Skills must scale.** Port OpenClaw's gating + budget so a big library never floods the prompt.

---

## 3. The phases

### Phase A — Complete the plugin FORMAT (cards + skills on plugins) · *low risk, additive*
**Goal:** a plugin can carry a `.md` card and bundled skills — uniform with internal tools.
- **Card:** loader reads `plugins/<id>/CARD.md` (frontmatter `summary` + body); a plugin's summary comes from its card, and a static body is contributed as a prompt section. **Add `CARD.md` to `plugins/google/`**. Fixes "tools have cards, plugins don't."
- **Skills:** a plugin's `plugins/<id>/skills/` is discovered and merged into `resolve_skills` (top-level `skills/` unchanged), so a plugin ships tools **and** skills.
- **Files:** `infrastructure/cards.py` (read plugin cards), `infrastructure/plugins/discovery.py` (collect plugin card + skill dirs), `main/container.py` (merge plugin skills into `resolve_skills`).
- **Tests:** plugin CARD.md → summary + static section; plugin skill dir → advertised in `## Skills`. **Safe:** purely additive; no existing tool/plugin changes.

### Phase B — Skill gating + budget (port OpenClaw) · *medium risk, fixes scaling*
**Goal:** 100 skills never flood the prompt. Port OpenClaw's gating + budget.
- **`requires` gate:** SKILL.md frontmatter `requires: { bins, env, config }` → skip the skill if a required binary/env/config is absent ([config-eval.ts parity](../../../reference/openclaw-main/src/shared/config-eval.ts)).
- **Activation gate:** a plugin's skills advertise only when the plugin is enabled/active — **free**, since a plugin's skills only exist if the plugin loaded.
- **Budget:** cap N skills / ~M chars; over budget → **compact format** (name + path, drop descriptions) → trim to fit. Config: `skills_prompt_max` / `skills_prompt_chars`.
- **Files:** new `domain/skills.py` or `infrastructure/skills/` (pure gate/budget + `requires` eval), `infrastructure/skills/file_skills.py` (parse `requires`), `prompt.py` `_skills_section` (apply budget/compact).
- **Tests:** requires-absent → hidden; budget exceeded → compact → truncate; gate unit-tested. **Safe:** defaults set high enough that current small skill sets are unchanged.

### Phase C — MCP hot-add (central registry, live) · *low risk, additive*
**Goal:** add an MCP server with no restart; one central registry.
- **`mcp.add` gateway method:** build `McpServerConfig` → connect live via the provider → `service.add_tools` (instant) → **persist to `config.mcp_servers`** (write `agentd.config.json`).
- **Terminal `/mcp add|list|remove`** (= `claude mcp add` / `openclaw mcp add`). `/mcp list` unifies config + plugin-MCP servers with status.
- **Files:** `infrastructure/tools/mcp/` (connect a single server live), `presentation/gateway.py` (`mcp.add`/`mcp.remove`/`mcp.list`), `clients/terminal/__main__.py` (`/mcp`).
- **Tests:** add → tool appears + persisted; remove → gone. **Safe:** additive; existing MCP discovery untouched.

### Phase D — Unified catalog VIEW · *low risk, additive* — ✅ done
`tools.list` tags each entry `internal` / `plugin:<id>` / `mcp:<server>` (`tool_source` in `agent_service.py`; the GuardedTool wrapper carries `.source`, stamped in the container + the gateway's MCP paths; plugin tools tagged `_plugin_id` at load) and shows each tool's **card summary** (cards threaded into the service as `tool_cards`). Terminal `/tools` renders the source tag (internal untagged); `/mcp list` from Phase C.

### Phase E — Plugin dependency injection (the ENABLER) · *medium-high risk* — ✅ done
`PluginContext` gained injected handles (`browser`, `computer`, `task_store`, `memory_bank`,
`resource_manager`, `credential_store`, `connect_token_store`) so a plugin can build a tool that
needs them — the SAME singletons the built-ins get. Threaded `container → discover_plugin_contributions(config, deps) → load_plugin_entry(…, deps) → PluginContext(**deps)`; unknown keys are filtered so adding a handle never breaks an older plugin. The bridge that lets complex internal tools become plugins — built WITHOUT moving any tool yet.

### Phase F — Migrate internal tools → plugins (BATCHES) · *the big move* — ✅ done
Every internal tool migrated to a built-in capability bundle under `plugins/` (move-not-duplicate),
in verified green batches: **F1** read/write/edit/ls/find→`core_fs`, exec/process→`shell`; **F2**
web_search/web_fetch (+ `search/`+`fetch/` subpackages)→`web`; **F3** heartbeat/outcome/cron/goal/
commitment→`autonomy`, memory→`memory`, skill_workshop→`skills`, resource→`resources`, verify→
`verify`, browser+login→`browser`, computer→`computer`. The Tool contract was relocated to
`application/interfaces/tool.py` (re-exported from infrastructure). Each bundle gates itself on
config/deps in its `register()` (via the Phase-E DI ctx). Built-ins always load from a fixed
`builtin_plugins_dir`. Per-tool cards stay in `tools/<name>.md` (keyed by name). ~30 test imports
retargeted via a `conftest.py` that puts every bundle dir on `sys.path`.

### Phase G — Core = framework only; tighten the contract · *final* — ✅ done
`build_tools` is **gone** — the catalog is assembled entirely by plugin discovery
(`discover_plugin_contributions`). The core (`agentd/`) contributes NO tool implementations; the
smoke check shows every tool sourced `plugin:<id>`, none `internal`. Import-linter gained a
**forbidden** contract: `agentd` ⊥ `plugins` (2 contracts kept). The injected browser/computer
*providers* stay in core (built by the container, handed to the browser/computer plugins via DI).

### Phase H — Optional upgrades (post-parity) — ✅ done
- **H1 entitlement + compatibility (the 4-gate model):** loading a plugin now requires *installed*
  (manifest found) + *enabled* (config/manifest) + *compatible* (`[requires]` os/bins/env) +
  *entitled* (an injected `EntitlementPolicy`, default `AllowAllEntitlement`, **fail-open**). The
  entitlement seam is the commercial hook (architecture, not billing).
- **H2 relevance-filtered skills:** optional embed + top-K-per-message ranker
  (`rank_skills_by_relevance`, pluggable embed fn, always-on skills kept, **fails open** to the full
  list). OFF by default (`skills_relevance_enabled`); wired at the prompt seam with the message
  threaded through `_build_prompt`.

---

## 4. Import-linter evolution (kept green throughout)

| Phase | Contract state |
|---|---|
| A–E | unchanged: `main > presentation > infrastructure > application > domain`. Plugins live outside `agentd/` (consumers of the framework, like tests). |
| F | unchanged while tools moved out — each migrated tool stopped being imported by `agentd/*` (stayed green every batch). |
| G ✅ | **Added** a `forbidden` contract: `agentd` ⊥ `plugins` (`include_external_packages = True`). The dependency rule still points INWARD — plugins import the framework (`Tool`/`ToolResult`/domain), the core never names `plugins`. **2 contracts kept, 0 broken.** |

The framework (`Tool`, `guard`, MCP client, loaders) **stays inside `agentd/`** — only tool
*implementations* leave. So "clean architecture" is preserved: the dependency rule still points
inward; plugins are the outermost ring.

---

## 5. Risk & rollback

- **Each phase is independently revertible** (A–E additive; F is per-batch; G is the contract flip).
- **The migration is invisible to agents:** they consume the assembled catalog (`_tools`) filtered per-turn — a tool works the same whether it's still in `build_tools` or already a plugin. So F can proceed tool-by-tool with no behavior change.
- **MCP and skills are de-risked first** (B, C) so the scaling/UX wins land before the core move.
- **DI (E) before complex-tool migration (F3)** — never move a tool whose deps aren't yet reachable from `ctx`.

---

## 6. Summary

| Phase | Delivers | Risk | Breaks anything? |
|---|---|---|---|
| **A** | plugin cards + bundled skills | low | no (additive) |
| **B** | skill gating + prompt budget (scales to 100s) | medium | no (defaults preserve current) |
| **C** | `/mcp add` hot-add (central registry) | low | no (additive) |
| **D** | unified `tools.list` view (source + card) | low | no (read-only) |
| **E** | plugin dependency injection (the enabler) | med-high | no (additive) |
| **F** | internal tools → plugins, in batches | high | no (per-batch, move-not-dup) |
| **G** | core = framework only; contract tightened | final | no (catalog parity) |
| **H** | relevance skills · distribution/entitlement | later | n/a |

**This cut: A + B + C** — the uniform plugin format (cards + skills), skill scaling (gating +
budget), and MCP hot-add. All additive, all green, delivering most of the "consistent + central +
on-par-with-OC" feel **before** any core code moves (D–G are the second campaign).
