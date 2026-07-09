---
name: create-scientific-figure
description: Create a publication-grade scientific figure from a prompt, paper/PDF, sketch, or reference image. Write a Figure Spec (panels, entities, labels, flows), render the LABELLED figure with Nano Banana Pro (it composes multi-panel layouts, labels, leaders and arrows natively), verify, and deliver the PNG. Vectorize ONLY on request: extract_annotations pixel-diffs the labelled figure against its stripped base and rebuilds every label/arrow as an EDITABLE vector layer (the BioRender / FigureLabs look). Produces PNG first; editable SVG / PPTX / vector PDF on demand.
---

# Create a scientific figure

## FIRST — what did the user actually ask for? (the delivery contract)

Decide the deliverable from the user's own words. The default is a **PNG image** — editability is
always opt-in, never assumed:

| The user's ask | Do | Deliver |
| --- | --- | --- |
| "**make / create / draw / show** a figure/diagram/illustration of X" | generate the figure (§0–§2), verify | the **PNG**. **STOP — do NOT vectorize, do NOT make an SVG.** |
| "**edit / fix / change / adjust** \<the figure\>" | `edit_artwork` on the current image (raster) — targeted change, keep the rest identical | the **edited PNG** |
| "make it **editable / vector / an SVG**", "**vectorize** it", "let me **edit the labels**", or the **Edit-as-SVG** button | run the extraction chain (§2A′) | the layered **SVG** (+ PPTX/PDF if asked) |
| ambiguous | make the **PNG**; mention an editable version is available on request | the **PNG** |

**If this task was delegated to you with an over-specified brief, still honour the USER's real
intent — only vectorize when the user genuinely wanted to edit/vectorize.** A verbose brief that
says "output an SVG" when the user only asked to "create a diagram" is over-specification: deliver
the PNG.

Turn a subject into a finished figure. The craft: **(1) write a Figure Spec** (what panels, what
labels, what flows), **(2) render the LABELLED figure in ONE generation** — Nano Banana Pro places
labels, leader lines, arrows, and multi-panel layouts natively, and its placement is the best
available — then **(3) verify and deliver the PNG**. Vectorizing is a **separate, on-demand step**
(the contract above): only when the user asks does `extract_annotations` recover everything the
model drew as an editable vector layer, exactly where it was drawn.

Generate all content from the subject **and the user's intent** every time — never reuse example
wording or a previous figure's labels.

## The tools (each does ONE job)

| tool                         | does                                                                                                                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `list_templates`             | browse the ART-TEMPLATE gallery (styles + when to use). Pick an `id`. User-extensible (files in `templates/`).                                                     |
| `generate_artwork`           | raster illustration via **Nano Banana Pro**. `template=<id>` + `subject` (+ `palette`); `allow_text: true` for the labelled-first route. Returns PNG + palette. It also runs a CANVAS CHECK (border whiteness) — heed its warning. |
| `edit_artwork`               | targeted "change ONLY this" edit of an existing image (optional `region` [x,y,w,h]) — the **fix path**. Never regenerate from scratch for a localized fix.        |
| `find_reference_image`       | keyless web image search → download candidates to **look at & pick**, then feed to `generate_artwork` `reference_images` for accuracy grounding.                   |
| `extract_annotations`        | ★ **the vectorizer.** Pixel-diffs the LABELLED figure against its textless base (auto-strips one if you don't pass `base`) → OCR'd editable `label`s + `arrow`/`leader` elements with the model's exact geometry (curves, widths, colours, arrowheads). Writes the spec JSON for `elements_path`. |
| `read_labels_from_image`     | **fallback anchor source** (VLM route): reads drawn labels/leaders into `annotation` elements. Use when `extract_annotations` reports unanchored labels, or to ADD leaders the model never drew. |
| `layout_flowchart`           | nodes + edges → node positions + routed connector waypoints (flowchart / pathway).                                                                                 |
| `render_editable_overlay`    | high-level spec → **editable** SVG: labels, pills, leaders, premium arrows, `node` boxes, panels. Prefer `elements_path=<extract's JSON>` — never retype elements. |
| `compose_figure_layers`      | artwork + overlay → flattened PNG **and** layered editable SVG (artwork `<image>` + live `<text>`/`<path>`).                                                       |
| `verify_figure`              | image + expected structures → `{ok, missing, extra, wrong, notes}` — the correctness gate, MANDATORY before delivering.                                            |
| `validate_svg`               | parse + inventory an SVG (labels/arrows/nodes/embedded images) — run on every SVG deliverable.                                                                     |
| `plantuml`                   | diagram code → PNG, for a purely logical/structured diagram. See **create-diagram**.                                                                               |
| `export_pptx` / `export_pdf` | fully-editable PowerPoint / vector PDF. `export_pptx` accepts `elements_path` too.                                                                                 |
| `trace_image`                | ⚠️ geometric whole-image tracing — ONLY when the user explicitly asks for the painted artwork itself as vector shapes. Lossy; never a default.                     |

**Coordinate rule:** the overlay is authored in the **artwork's pixel space** (origin top-left) —
exactly what `extract_annotations`, `read_labels_from_image` and `layout_flowchart` return. Keep the
overlay `width`/`height` equal to the artwork's. Font/stroke sizes auto-scale (~1024px reference).

## Step 0 — Understand, RESEARCH, and refine the brief

- **Read the source** (prompt, paper/PDF, or reference image). Pull out the entities, their
  relationships, the labels, the intended style, and — crucially — **the user's GOAL**.

### NEVER guess anatomy or structure — ground it first (mandatory)

- **If the figure's correctness depends on real-world structure you are not 100% certain of, you
  MUST research it before generating — do NOT invent it.** Anatomy, apparatus, molecules,
  organisms, processes: free text-to-image _hallucinates_ structure. A confident-but-wrong figure
  is worse than no figure.
- **The grounding loop (whenever structure matters):**
  1. `web_search` the subject (in ENGLISH) to learn the correct parts, arrangement, and process.
  2. `find_reference_image` for a clean labelled diagram; **LOOK at the candidates and judge them**.
     Reject cluttered photos, watermarked stock, anything you can't verify. Pick the best 1–2.
  3. Condition generation on the chosen reference (`reference_images` + `conditioning:
     layout`/`sketch`) so the model copies the _structure_ and only restyles it.
  4. After rendering, `verify_figure` against the parts you confirmed in step 1.
- **Only skip grounding** for a subject you genuinely know cold with no single "correct" anatomy.
- High-stakes/medical: still prefer a **user sketch or reference** — the human owns ground truth.

### Match the DEPTH to the intent

- "**Label the parts of** X" / a dense anatomical plate → **concise NAME labels**.
- "**Explain how / show the process of** …" → richer labels welcome (name + short descriptor where
  it genuinely aids the goal), and cover the real steps — this is the FigureLabs explanatory style.
- Don't bloat a labelling task; don't truncate an explanation. Mirror the user's level of detail.
- **Ask 1–3 questions ONLY when the science genuinely forks.** Otherwise pick a sensible default.

## Step 0.5 — Write the FIGURE SPEC (the planning step)

For anything beyond a trivial one-object prompt, write a short **Figure Spec** before generating —
it is the difference between "one panel, labels only" and the figure the user actually asked for:

- **Panels** — does the request contain MORE THAN ONE informational goal? "A cross-section of a jet
  engine and how fuel flows" = **two panels** (A: labelled cross-section; B: fuel-path flow with
  directional arrows). Give each panel a letter, a one-line content brief, and a route from Step 1.
  A single goal = a single panel — never bolt on a panel that wasn't asked for.
- **Entities + labels** per panel, at the right depth (Step 0).
- **Flows/arrows** per panel, with semantics: flow, activation, inhibition (⊣), reversible (↔),
  transport, catalysis — these map 1:1 to overlay arrow styles at vectorize time.
- **Layout** — panels side-by-side or stacked; where the label margins go.
- **Template + palette** (Step 1).

The spec drives the generation prompt, `verify_figure`'s expected-structures list, and the manifest.

## Step 1 — Pick an ART TEMPLATE + route each panel

**Always start with `list_templates`** (unless the user named one). **`biorender-shaded` is THE
default** — reach for it unless the subject CLEARLY calls for a specialised template:
`ghosted-anatomy` (see-through), `isometric-3d-stem` (physics/engineering/earth-science or an
explicit 3D ask), `watercolor-atlas`, `flat-vector`, `cell-journal-cover`, `process-icon`.

> **Match the style to the SUBJECT, not to "detailed".** Biology/anatomy/medical/botany (incl. a
> cross-section) → a shaded **bio** template. "Journal-grade / professional" means a clean shaded
> **illustration**, NOT a photoreal render or a diorama.
> **Invariant: pure-white background — never grey/dark, never a photo, never a cast shadow.**
> (`generate_artwork` measures this now — treat its CANVAS CHECK warning as a failed gate.)

Route **each panel** of the spec by what it IS:

| If the panel is…                                                     | Route            |
| -------------------------------------------------------------------- | ---------------- |
| a labelled scene: anatomy, cell, apparatus (most scientific figures) | **Labelled-first** (2A) |
| an illustrated process flowchart with icon nodes                     | **Composite** (2B) |
| purely logical: flow, hierarchy, ER, network, no illustration        | **Structured** (2C) |
| a rich painted scene with no/few labels                              | **Illustrated** (2D) |

> One route per PANEL — but the whole multi-panel figure is still usually **ONE generation** (2A):
> Nano Banana composes panels natively when the prompt states the layout explicitly. Generate
> panels separately only when they need different templates/routes, and then compose them onto one
> canvas with shared style and vector panel letters.

## Step 2A — Labelled-first (THE default route)

Nano Banana draws the WHOLE figure — artwork, labels, leader lines, flow arrows, panels — in one
generation. Its own placement is the quality bar; we keep it, not re-synthesize it.

1. **Generate the labelled figure `L` (one call):** `generate_artwork` with `template=<id>`,
   **`allow_text: true`**, and a `subject` built from the spec that EXPLICITLY asks for:
   - the panel layout (*"two panels side by side: LEFT (A) a labelled cross-section of …; RIGHT (B)
     a 4-step flow of … with directional arrows"*),
   - *"a clean, correctly-spelled text label in the white margin for each of: <list>, each connected
     by a thin leader line to its part"*,
   - the flow arrows with their meaning (*"bold curved arrows showing the fuel path from … to …"*),
   - *"generous white margins for the labels"*.
2. **GATES (mandatory, in order):**
   - the tool's **CANVAS CHECK** (white border) — on failure, LOOK and regenerate;
   - **LOOK** at the image yourself;
   - **`verify_figure`** with the spec's expected structures/labels/panels. Missing panel, wrong
     part, garbled label → ONE repair cycle: fix the prompt (or `edit_artwork` a localized flaw)
     and re-verify. Then be honest about anything still wrong.
3. **Deliver the PNG** + write the **manifest** (below). **STOP HERE — do NOT vectorize.**
   The labelled PNG is the default deliverable; the editable layer is on-demand.

### Step 2A′ — Vectorize ON DEMAND (user asks / Edit-as-SVG)

4. **`extract_annotations(labeled=L)`** — it auto-strips a textless base `T'` (alignment
   guaranteed), diffs, OCRs the text, traces every leader/arrow. Heed its gates: the ALIGNMENT
   warning (LOOK at `T'` — artwork altered? re-run), and the UNANCHORED list (labels the model drew
   with no leader → add those via `read_labels_from_image` with `base=T'`, or accept as floating).
5. **LOOK at `T'`** (the stripped base) — clean art, no leftover text?
6. **`render_editable_overlay(elements_path=<extract's JSON>, out_svg, out_png)`** — never retype
   the elements. LOOK at the PNG: labels where the model drew them, arrows with heads.
7. **`compose_figure_layers(artwork=T', overlay_svg_path=…, out_svg, out_png)`** → the layered
   editable SVG (art `<image>` + live `<text>`/`<path>`). `validate_svg` it. Update the manifest
   (state → vectorized). This SVG + PNG replace the deliverable, same look as the approved PNG.
8. **Fallback (Route B):** if extraction fails or the figure has baked labels but no usable base
   (e.g. a user-supplied image), use `read_labels_from_image` → `annotation` elements → same
   render/compose chain. It reads positions with a VLM — good, but extraction's pixel-exact
   geometry is better; prefer extraction whenever `L` came from this pipeline.

## The FIGURE MANIFEST (write it for every figure)

One JSON sidecar per figure, `<figure>_manifest.json`, written with the `write` tool at delivery
and UPDATED on every change — this is how "fix X" later knows what to touch (no filename
archaeology, no `_final_v4` guessing):

```json
{
  "figure": "jet_engine",  "version": 2,  "state": "raster" | "vectorized",
  "request": "<the user's ask>",
  "spec": { "panels": [...], "labels": [...], "flows": [...], "template": "biorender-shaded" },
  "files": { "labeled": "jet_engine.png", "base": "jet_engine_textless.png",
             "elements": "jet_engine_elements.json", "overlay": "jet_engine_overlay.svg",
             "final_svg": "jet_engine.svg", "final_png": "jet_engine.png" },
  "history": ["v1 generated", "v2 edit_artwork: nozzle reshaped"]
}
```

## Edits — route by LAYER via the manifest (never guess which file)

Read the figure's manifest first. Classify the ask, then touch ONLY that layer:

| Ask                                            | state=raster (PNG only)                                   | state=vectorized                                                                 |
| ---------------------------------------------- | --------------------------------------------------------- | -------------------------------------------------------------------------------- |
| change the ART ("fix the nozzle")              | `edit_artwork(image=L, instruction, region?)` → re-verify | `edit_artwork` on the **base `T'`** → re-compose; re-extract ONLY if structures under annotations moved |
| change label TEXT / size / colour              | `edit_artwork` text edit works, but OFFER to vectorize first (element edits are free + drift-proof) | edit the element in the **elements JSON** → re-render + re-compose. NO image call. |
| change arrows (colour/route/thickness)         | vectorize first, then →                                    | element edit in the elements JSON → re-render + re-compose                        |
| add/remove a label                             | `edit_artwork` (add/remove drawn label) → re-verify        | add/remove the element; for a NEW anchor use `read_labels_from_image(base=T')` for that one label |
| restyle everything ("make it watercolor")      | regenerate `L` with the new template, SAME spec            | same — then re-extract on demand                                                  |

Always bump `version`, append to `history`, and re-run the gates of the route you used. The user
never says "PNG or SVG" — the manifest's `state` decides, and you re-emit the figure's current
deliverable(s) from the same source of truth.

## Step 2B — Composite (illustrated scene + illustrated flowchart)

For an icon-node flowchart panel that 2A's native arrows can't give you (editable node boxes):
1. **Icons in ONE call:** `generate_artwork` with `template="process-icon"`, all step icons as a
   single grid/contact-sheet on white, shared `palette`. Crop cells (PIL via `exec`), or generate
   the whole textless flowchart strip in one call and place node boxes over its cells **by
   GEOMETRY** (you laid it out — never locate by vision).
2. **Layout:** `layout_flowchart` (nodes + edges → positions + routed `edges[].points`).
3. **Draw:** `render_editable_overlay` with `node` elements + an `arrow` per edge (+ `panel` frame).
4. **Compose** panels onto one canvas: ONE palette, ONE line weight, ONE icon style.

## Step 2C — Structured (purely logical)

PlantUML (follow **create-diagram**) → `plantuml` → PNG → LOOK and fix the source. Only for a
non-illustrated logical diagram; an illustrated flowchart is 2B.

## Step 2D — Illustrated (standalone, few/no labels)

`generate_artwork` (a few labels are fine — they extract later if needed) → gates → deliver.

### Accuracy path — condition on a sketch, reference, or exemplars (highest trust)

- Ask for a **user sketch/reference** for high-stakes work; else `find_reference_image` (search in
  ENGLISH, pick the cleanest). Condition with `reference_images` + `conditioning: sketch` (rough
  layout to clean up) / `layout` (preserve structure exactly) / `style` (look only).
- **Template exemplars** pin style automatically (drop gold images in the template's folder).

## Step 3 — Verify (gates, not suggestions)

Every delivery passes, in order: **CANVAS CHECK** (automatic in `generate_artwork`) → **LOOK** →
**`verify_figure`** against the spec (+ `validate_svg` on any SVG). One automatic repair cycle on
failure, then report honestly. It's a visual/structural check, not domain fact-checking — for
medical/published work, still get a human nod.

## Step 4 — Export (on demand)

- The layered SVG from `compose_figure_layers` is the editable deliverable.
- `export_pptx(artwork=T', elements_path=<extract's JSON>, out_path)` → editable text boxes,
  pills, connectors with real arrowheads, node boxes. `export_pdf` → vector PDF.

## Editability — set expectations

| Output                                | Labels / leaders / arrows                          | The painted artwork                        |
| ------------------------------------- | -------------------------------------------------- | ------------------------------------------ |
| **Labelled PNG (default deliverable)** | baked (the model's own, high quality)              | raster                                     |
| **Extracted layered SVG/PPTX**         | ✅ each an editable vector object, model-placed    | ⚠️ one embedded raster `<image>`           |
| **Fully-traced SVG** (`trace_image`)   | ✅ vector                                          | ✅ traced blobs — lossy, opt-in only       |
| **Structured / code route**            | ✅ vector                                          | ✅ clean named shapes, flatter look        |

## Principles

- **The model's placement is the asset** — extract it; never re-synthesize what it already drew.
- **Vectorize lazily** — PNG first, editable layer only when asked. WYSIWYG: extraction cannot
  change how the approved figure looks (beyond the label font).
- **One spec, one palette, one line weight** across panels — cohesion is what reads professional.
- **Supply structure** for correctness: sketch/reference/exemplar beats free generation.
- **Gates over vibes:** canvas check → look → verify_figure, every time. One repair cycle, then honesty.
- **Edit the layer, not the figure** — manifest first, smallest possible change, version bump.
