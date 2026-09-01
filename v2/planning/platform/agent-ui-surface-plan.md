# Agent UI Surface — declarative per-agent app skins (figurelabs++ without hardcoding)

*Research date: 2026-07-12. Sources: fresh figurelabs.ai live-site sweep, full seam verification of
the desktop client + gateway, and a figure-stack tool inventory. Builds on
[../figures/figurelabs-parity-plan.md](../figures/figurelabs-parity-plan.md) (pipeline side, P1–P4.5
built) — this plan is the missing APP-SURFACE side.*

**Status: design — awaiting LGTM. No code yet.**

---

## 1. The one idea

The client ships a small fixed vocabulary of **generic widgets** (gallery, selector, palette,
slider, toggle, form, button-group, preset-list, region-picker), rendered by ONE generic
`<SurfaceRenderer>`. **Which** widgets appear, **what data** feeds them, and **which tool** each
interaction invokes is declared as *data* in the agent's / plugin's own files and discovered at
handshake. No agent name, no figure-specific branch, ever appears in client code. Install
figure-creator → its Templates/Style/Export panels appear; uninstall → gone; a future
slides-creator brings its own skin the same way.

This is the exact pattern that already powers the "Convert to Vector" canvas button
(`artifact_action` → `plugins.catalog` → generic `ActionTabs` → `tools.invoke`, no LLM) —
generalized from "buttons on an artifact" to "panels for an agent".

---

## 2. Verified seams (what already exists — file:line, checked 2026-07-12)

| Seam | Where | State |
|---|---|---|
| `Tool.artifact_action` `{mime[], label, param}` | `v2/agent_runtime/application/interfaces/tool.py:108` (class at :64) | ✅ exists; client adds `tool` key itself (`lib/artifacts.ts:25-30`) |
| Tool self-description: `plugin`, `needs_model`, `default_model`, `model_kind`, `provider_options`, `provider_chain` | `tool.py:86-101` | ✅ all present |
| `plugins.catalog` RPC emits `artifactAction` per tool | `gateway.py:2404-2466` (field at :2451) | ✅ |
| `tools.invoke` RPC (no-LLM direct tool run) | `gateway.py:1198-1221` | ✅; gate at :1213 admits ONLY tools declaring `artifact_action` |
| `capabilities.list` RPC + `CapabilityDescriptor` with free-form `extra` dict | `gateway.py:2302-2327`, `application/capabilities.py:16-26` | ✅ backend-complete; **client never calls it** (only a dead TS type `protocol.ts:112-122`); `agentId` param scopes *skills only* |
| Agent parsing: `AgentSpec` frozen dataclass + `FileAgentRegistry._load_dir` | `domain/agent.py:20-65`, `infrastructure/agents/file_registry.py:136-235` | ✅ but **fixed keys only — unknown TOML tables are DROPPED** (needs a new field for `[[ui.panel]]`) |
| Client store: `artifactActions` slice / `refreshArtifactActions` / `runArtifactAction` | `store.ts:293` / `:548-570` / `:790-838` | ✅; refresh runs in `handshake()` (:542) |
| Handshake on connect: `hello` → sessions/recents/projects → bundles → `plugins.catalog` | `store.ts:530-543` | ✅; `currentAgentId` at `store.ts:270` |
| Generic data→widget renderers to model after | `ActionTabs` `canvasViewers.tsx:14-35`; Settings provider-picker (`SettingsView.tsx:722-746,1023-1058`); declarative `lib/settingsSchema.ts` schema→form; `models.list` descriptor-driven pickers (`gateway.py:2329-2402`) | ✅ the pattern is native to this client |
| Canvas viewer selection | `CanvasBody` switch `canvasViewers.tsx:311-325` + extension map `lib/canvasFile.ts:37-57` | ⚠ hardcoded switch, not a registry (acceptable: viewers are client primitives, like widgets) |
| Shell slot for a panel | `App.tsx:30-57` (3-col: Sidebar + main + Canvas `<aside>`) | ❌ no dock exists — one new slot needed |

### Corrections vs the prior hand-off synthesis
1. `artifact_action` has **no `tool` key** server-side; the owning tool is implicit.
2. `capabilities.list` `agentId` scoping applies to **skills only** today; agents/plugins/tools rows
   are unscoped. Panels should ride each **agent descriptor's** `extra`, so no scoping change needed.
3. `AgentSpec` is a **frozen dataclass with fixed keys** — `[[ui.panel]]` tables parse but are
   discarded; a real `ui_panels` field + parse is required (small).
4. There is **no existing side panel/dock** to reuse; the Canvas aside is artifact-only. The surface
   panel is one new shell slot (built once, generic).
5. Widget→tool actions need a small **context-binding vocabulary** the hand-off didn't spec: e.g.
   region-redraw needs the active artifact's path + a user-drawn region. Descriptors must be able to
   bind params to client context (`$activeArtifact.path`, `$selection.region`), not just widget values.

---

## 3. FigureLabs — fresh sweep deltas (2026-07-12)

Prior map holds (chat wrapper over 3rd-party models; Text Edit / Region Redraw / BG Remove /
Upscale 2K-4K-8K; 150cr vectorizer = most expensive op; view-only gallery; prompt templates live in
a blog; **still NO icon library, in-app template gallery, drag-drop editor, property panels, data
charts, multi-panel builder, API, or team features**). New since the last pass:

- **Reference-to-Figure** input mode (match style+layout of a reference image). *We have the tool
  plumbing already (`generate_artwork.reference_images` + `find_reference_image`) — needs only a UI
  upload affordance.*
- **Built-in vector canvas promoted to a headline Starter+ feature** (tutorial "Edit on Vector
  Canvas") — they're marketing the canvas harder; depth still unclear.
- Pricing now 4 tiers ($0/$10/$20/$54), daily refresh credits (50–100/day), free tier limited to 1K
  export + "SVG flowcharts"; **Publication Authorization** (commercial rights) gated to paid tiers.
- Claims 400K+ researchers; heavy SEO blog push; a clone competitor exists (**figpad.ai**).

## 4. Gap matrix (what figure-creator lacks as an APP, post-P4.5)

| # | Gap | Bar | Tier |
|---|---|---|---|
| 1 | Template gallery in the UI (data exists server-side: 10 TOMLs, `catalog()` client-shaped) | beats figurelabs (they have none) | A |
| 2 | UI selectors as chrome: template/style/provider/aspect/palette/resolution | figurelabs weak here | A |
| 3 | Export matrix: format buttons + **DPI/journal-size presets (nothing exists — PPTX fixed 96dpi `figexport_common.py:1`, no dpi/journal params on any export tool)** | SciDraw/MTG | A (needs small tool-param work) |
| 4 | Prompt-preset library as clickable UI (their S.S.V.D. blog, in-app) | figurelabs (blog only) | A |
| 5 | Region Redraw / Text Edit as UI gestures (tools exist: `edit_artwork` w/ `region [x,y,w,h]`) | figurelabs | A/B (region-picker widget + context binding) |
| 6 | Property inspector on vectorized elements (elements JSON exists from `figure_to_svg`; needs addressable IDs + a deterministic `edit_elements` tool — element edits are currently a skill convention only) | BioRender | B |
| 7 | Persistent direct-manipulation canvas (move/resize/undo/layers) | BioRender; figurelabs punts to Illustrator | C |
| 8 | Searchable scientific icon/asset library | BioRender 50k icons | content track, arch-independent |
| 9 | Data charts (CSV→plot) & multi-panel builder UI | BioRender/SciDraw | later; `compose_panels` is P5 in the parity plan |

Where we already lead (unchanged): layered SVG with live `<text>` + real editable PPTX shapes +
traced centerline arrows as first-class `arrow`/`leader` elements vs their whole-image blob trace;
BioRender AI figures are flat PNGs with no SVG at all.

---

## 5. Architecture

### 5.1 Declaration (pure data, in the capability's own files)
Two symmetric entry points:

- **Agent-level skin** — `[[ui.panel]]` tables in `agent.toml`, parsed into a new
  `AgentSpec.ui_panels: tuple[dict, ...]` field (`file_registry._load_dir` maps it like `suggestions`).
- **Plugin/tool-level control** — a `ui_surface: dict` class attr on the `Tool` contract, sibling of
  `artifact_action` (`tool.py:108`). Plugin installed → its widget appears; removed → gone.

Descriptor sketch (vocabulary, not final schema):

```toml
[[ui.panel]]
id = "figure-templates"
title = "Templates"

[[ui.panel.widgets]]
kind = "gallery"
source = { tool = "list_templates" }                     # read-only tool feeds the data
action = { tool = "generate_artwork", param = "template" }

[[ui.panel.widgets]]
kind = "selector"
label = "Style"
source = { param_enum = ["generate_artwork", "style"] }   # or inline options = [...]
action = { tool = "generate_artwork", param = "style" }

[[ui.panel.widgets]]
kind = "region-picker"
label = "Redraw region"
action = { tool = "edit_artwork",
           bind = { image = "$activeArtifact.path", region = "$selection.region" },
           param = "instruction" }                        # free-text goes in `param`
```

Context bindings (`$activeArtifact.path`, `$selection.region`, widget values) are the only client
vocabulary beyond the widget kinds; both lists are closed, versioned, and rendered generically.

### 5.2 Transport (reuse `capabilities.list` + `plugins.catalog`)
- Agent panels → the agent's `CapabilityDescriptor.extra["ui"] = {panels: [...]}` in
  `_capability_descriptors` (`capabilities.py:38-42` builds agent extras today).
- Tool `ui_surface` → emitted by `plugins.catalog` next to `artifactAction` (`gateway.py:2451`).
- Client: `handshake()` additionally calls `capabilities.list` (kind=agent) — the RPC and the TS
  type already exist; this is the first real consumer.

### 5.3 Client (one renderer + one slice + one slot — built once)
- `uiPanels` store slice beside `artifactActions` (`store.ts:293`), populated at handshake,
  filtered by `currentAgentId`.
- `<SurfaceRenderer>` — iterates panel descriptors → widget kit (~8 primitives). Direct
  generalization of `ActionTabs` + the `settingsSchema` form renderer.
- Shell slot: a new collapsible `<aside>` (or Canvas-adjacent dock) in `App.tsx`, rendered ONLY
  when the current agent has panels. Chat+canvas shell untouched for agents without a surface.

### 5.4 Actions (reuse the no-LLM rail; broaden the gate)
Every interaction → `tools.invoke` (`gateway.py:1198-1221`), same path as `runArtifactAction`
(`store.ts:790`). Broaden the `:1213` gate from "declares `artifact_action`" to "declares
`artifact_action` OR `ui_surface` OR is referenced by an installed agent's `ui.panel` action" —
still a declared allowlist, never arbitrary tools. Results reuse the existing open-artifact flow.

### 5.5 Invariants (the no-hardcode contract)
- Client code may know **widget kinds and binding names**, never agent ids, tool names, or domains.
- All activation is capability-presence: panel shown ⇔ its agent is current ∧ its tools installed
  ∧ enabled (same gating as `refreshArtifactActions` `store.ts:548-570`).
- Descriptors are discovered at handshake and refresh on reconnect — no client rebuild to add/change
  a skin.

---

## 6. Phased build

| Phase | Scope | Notes |
|---|---|---|
| **S1 — rail** | `AgentSpec.ui_panels` + parse; `Tool.ui_surface`; `extra.ui` in capabilities; broaden `tools.invoke` gate; client `capabilities.list` call + `uiPanels` slice + `<SurfaceRenderer>` + widget kit + shell slot | the one-time client build; after this all UI is data. Daemon restart + client rebuild. |
| **S2 — figure skin as data** | figure-creator `[[ui.panel]]`s: Templates gallery (add a `preview` image per template — `catalog()` lacks one; `has_exemplars` exists), Generate selectors (template/style/aspect/palette/provider + **add `resolution` to `generate_artwork` params schema** — honored at `:275` but missing from schema), Export buttons, Prompt presets (new `presets.toml` + tiny list tool), Reference-image upload affordance | zero new client code |
| **S3 — export matrix** | `dpi` + journal-size presets (Nature/Cell mm, px@dpi) as params on `export_pptx`/`export_pdf`/`render_svg` + preset data file; export form widget | closes gap #3 |
| **S4 — inspector (Tier B)** | addressable element IDs in `figure_to_svg` elements JSON + deterministic `edit_elements` tool (mutate → re-render overlay → compose, no LLM) + property-inspector widgets | passes figurelabs |
| **S5 — canvas (Tier C)** | direct-manipulation vector canvas fed by elements JSON, generic for any agent emitting an object graph | leadership move; figurelabs never built this well — sequence last |
| **∥ content** | curated scientific asset/icon library | independent of the architecture |

## 7. Open decisions (need user input)
1. Shell placement of the surface panel: separate collapsible aside vs a dock inside the Canvas.
2. Widget-kit v1 scope: the 8 primitives above, or start with 4 (gallery/selector/button-group/form)?
3. Template previews: pre-render a thumbnail per template TOML (one-time `generate_artwork` run,
   committed as assets) vs live-render on first view.
4. Whether tool `ui_surface` (plugin-level) ships in S1 or agent-level panels alone suffice first.

## 8. Note on working-tree state (observed 2026-07-12)
Uncommitted diff on `v2/plugins/vectorize/figure_to_svg_tool.py` replaced the VLM semantic-arrow
reader with fully deterministic line tracing (`_stroke_to_line_element`: skeleton → RDP centerline →
arrowhead from width profile; result key `semantic_arrows`→`lines`). Arrows are still first-class
editable `arrow`/`leader` elements (not blobs) — the figurelabs edge stands — but the parity plan's
§P4.5 description ("VLM reads each arrow's direction") is now stale and should be updated when this
commits.
