---
name: create-scientific-figure
description: Create a publication-grade scientific figure from a prompt, paper/PDF, sketch, or reference image. Pick an ART TEMPLATE (the style gallery — biorender-shaded, ghosted-anatomy, isometric-3d-stem, flat-vector, watercolor-atlas, …), render textless artwork with Nano Banana Pro, then add labels, leaders, arrows, and an illustrated flowchart as an EDITABLE vector layer (the BioRender / FigureLabs look). Produces editable SVG / PPTX / vector PDF / PNG.
---

# Create a scientific figure

Turn a subject into a finished figure. The craft is: **(1) pick the right art template**, **(2)
render clean TEXTLESS artwork from it with Nano Banana Pro**, and **(3) add every label, arrow, and
flowchart node as an EDITABLE vector layer** — not baked into pixels. That separation is what gives
both the look _and_ correctness/editability. It is how we match (and beat) FigureLabs.

Generate all content from the subject **and the user's intent** every time — never reuse example
wording or a previous figure's labels.

## The tools (each does ONE job)

| tool                              | does                                                                                                                                                                                                                                                                |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_templates`                  | browse the ART-TEMPLATE gallery (styles + when to use). Pick an `id`. The gallery is user-extensible (files in `templates/`).                                                                                                                                       |
| `generate_artwork`                | textless raster illustration via **Nano Banana Pro (Gemini 3 Pro Image)**. Pass `template=<id>` + `subject` (+ optional `palette`); the template supplies style/palette/aspect/model and conditions on its exemplars. Returns the PNG **and the palette to reuse**. |
| `find_reference_image`            | keyless web image search → download candidate reference images to **look at & pick**, then feed to `generate_artwork` `reference_images` for accuracy grounding.                                                                                                    |
| `read_labels_from_image`                     | ★ **native-label anchor source.** A LABELLED oracle (an EDIT of your textless art) → ready-to-draw `annotation` elements by READING its drawn labels: text + pointer tip + label position (`at`). Pass `base`=the textless art (coords come back in its space, pointer snapped to its ink). Reliable — traces drawn lines, not anatomy. |
| `layout_flowchart`                     | nodes + edges → node positions + routed connector waypoints (for a flowchart or a pathway).                                                                                                                                                                         |
| `render_editable_overlay`                  | high-level spec → **editable** SVG: labels, pill labels, leader callouts, premium arrows, **`node` flowchart boxes with embedded icons + step badges**, panels.                                                                                                     |
| `compose_figure_layers`                  | artwork + overlay → flattened PNG **and** layered editable SVG (artwork `<image>` + live `<text>`/`<path>`/`<image>` icons).                                                                                                                                        |
| `verify_figure`                   | image + expected structures → `{ok, missing, extra, wrong, notes}` (the correctness gate; a TEXT verdict you can always read).                                                                                                                                      |
| `validate_svg`                    | parse + inventory an SVG (labels/arrows/nodes/embedded images).                                                                                                                                                                                                     |
| `plantuml`                        | diagram code → PNG, for a purely logical/structured diagram. See **create-diagram**.                                                                                                                                                                                |
| `export_pptx` / `export_pdf`      | fully-editable PowerPoint (text boxes, pills, connectors, node boxes + icon pictures, arrowheads) / vector PDF.                                                                                                                                                     |
| `trace_image`                     | ⚠️ **NOT part of creating a figure** — geometric shape-tracing for the separate **vectorize-figure** job (only when the user explicitly asks to trace artwork into vector shapes). Lossy; never a default.                                                                                                                                                                  |

**One rule that makes everything line up:** the overlay is authored in the **artwork's pixel
coordinate space** (origin top-left) — exactly what `read_labels_from_image` and `layout_flowchart` return. Keep
the overlay `width`/`height` equal to the artwork's. **Font/stroke/dot sizes auto-scale to the
resolution** (authored in ~1024px reference units), so you do NOT hand-tune font sizes for a 2K/4K
artwork — labels come out proportionate automatically. `export_pptx` uses the same rule. (Override the
multiplier with `scale` on `render_editable_overlay`/`export_pptx` only if you want chunkier or finer labels.)

## Step 0 — Understand, RESEARCH, and refine the brief

- **Read the source** (prompt, and ingest a paper/PDF or reference image if given). Pull out the
  entities, their relationships, the labels, the intended style, and — crucially — **the user's GOAL**.

### NEVER guess anatomy or structure — ground it first (mandatory)

- **If the figure's correctness depends on real-world structure you are not 100% certain of, you MUST
  research it before generating — do NOT invent it.** Anatomy, apparatus, molecules, organisms,
  processes: free text-to-image _hallucinates_ structure (wrong parts, wrong counts, wrong layout — a
  "corn kernel" that is anatomically nonsense). A confident-but-wrong figure is worse than no figure.
- **The grounding loop (do this whenever structure matters):**
  1. `web_search` the subject (in ENGLISH) to learn the correct parts, their arrangement, and the real
     process — so you know what a _correct_ figure must contain.
  2. `find_reference_image` for a clean labelled diagram/schematic; **LOOK at the candidates and judge
     them** — is this the right organism/organ/apparatus? are the parts correct and clearly drawn? Reject
     cluttered photos, watermarked stock, or anything you can't verify. Pick the best 1–2.
  3. Condition generation on the chosen reference (`reference_images` + `conditioning: layout`/`sketch`)
     so the model copies the _structure_ and only restyles it. Say you used a web reference.
  4. After rendering, `verify_figure` against the parts you confirmed in step 1.
- **Only skip grounding** for a subject whose structure you genuinely know cold and that has no single
  "correct" anatomy (e.g. "a phospholipid bilayer", "three boxes in a row"). When unsure, ground.
- If it's high-stakes/medical, still prefer a **user sketch or reference** — the human owns ground truth.

### Match the DEPTH to the intent — don't under- or over-explain

- **Read what the user actually wants and scale to it.** There is no fixed rule; judge per request:
  - "**Label the parts of** a chloroplast" / a densely-labelled anatomical plate → **concise NAME
    labels** ("Thylakoid", "Stroma"). Many structures = keep each label short so they all fit and read
    cleanly. Descriptors here would crowd the figure and force you to drop labels — don't.
  - "**Explain how / show the process of** …" (an explanatory or teaching figure) → **richer labels are
    welcome** — a name plus a short descriptor where it genuinely aids understanding ("Cotyledon —
    oil-rich storage tissue"), and cover the real steps/sub-steps of the process rather than a truncated
    list. This is where FigureLabs' descriptive style fits.
  - Add a descriptor to an **individual** label only when the term is non-obvious or the descriptor
    carries real information for THIS figure's goal — never as a blanket default on every part.
- So: a parts-diagram gets clean names; an explanatory figure gets depth. Don't bloat a labelling task,
  and don't truncate an explanation. When unsure, mirror the user's own level of detail.
- **Gate the effort by complexity:** trivial one-object prompt → generate. Multi-entity / "publication"
  → write a short **Figure Spec** (entities, relations, labels at the right depth, layout, style).
- **Ask 1–3 questions ONLY when the science genuinely forks.** Otherwise pick a sensible default.

## Step 1 — Pick an ART TEMPLATE + route to a machine

**Always start with `list_templates`** (unless the user named a template). **`biorender-shaded` is THE
default** — the clean shaded BioRender / *Cell*-journal look (soft gradient volume, white background) that
most scientific figures should use. **Reach for it unless the subject CLEARLY calls for a specialised
template:** `ghosted-anatomy` (see *through* an outer layer — surgical/procedural), `isometric-3d-stem`
(physics / engineering / earth-science, or an explicit 3D/isometric ask), `watercolor-atlas` (hand-painted
Netter atlas feel), `flat-vector` (a deliberately flat schematic, no shading), `cell-journal-cover` (a hero
cover). The template decides the whole art direction; you never hand-write style words. When in doubt,
`biorender-shaded`.

> **Match the style to the SUBJECT, not to "detailed".** Biology / anatomy / medical / botany (incl. a
> plant or organ **cross-section**) → a shaded **bio** template (`biorender-shaded`, `ghosted-anatomy`,
> `watercolor-atlas`). Reserve `isometric-3d-stem` for physics / engineering / earth-science, or when the
> user explicitly asks for a 3D / isometric view. "Journal-grade / professional / highly detailed" means
> a clean shaded **illustration**, NOT a photoreal 3D render or a museum diorama.
> **Invariant: the artwork ALWAYS renders on a pure-white background — never grey/dark, never a photo,
> never a cast/ground shadow.** (The tool enforces this too, but request it, don't fight it.)

Then route by what the figure IS:

| If the figure is…                                                         | Route            | Why                                                      |
| ------------------------------------------------------------------------- | ---------------- | -------------------------------------------------------- |
| a rich labelled scene: anatomy, cell, apparatus (most scientific figures) | **Native-label** | Nano Banana places the labels (correct); we re-draw them as an editable overlay over textless art |
| a scene **plus a process flowchart** (e.g. anatomy + how-it's-made)       | **Composite**    | one template/palette across art + illustrated flowchart  |
| purely logical: flow, hierarchy, ER, network, no illustration             | **Structured**   | a layout algorithm makes it exact                        |
| a rich painted scene with **no/few labels**                               | **Illustrated**  | one generation, fast                                     |

> **Pick exactly ONE route — a flowchart and its icons are NOT default.** Build a flowchart only when
> the request genuinely involves a **process / sequence of steps** ("how X is made", "the pathway
> of…", "the steps to…"). A parts diagram, a single labelled anatomy/structure, or a static scene is
> **Hybrid** — no flowchart, no icons. Never bolt a process panel onto a figure that doesn't ask for one.

## Step 2A — Native-label (the ONE route for labelled figures)

The labels are placed by **Nano Banana** (it drew the anatomy, so it knows where everything is), then
**re-drawn as our editable vector text/arrows over a clean TEXTLESS base**. No text is ever baked into the
base we keep, so nothing is erased and nothing can be destroyed. Do NOT try to GUESS structure positions from
the artwork, and NEVER erase / paint white over a labelled image to remove text (that whites-out the art). The flow:

1. **Textless base `T` (one call):** `generate_artwork` with `template=<id>` + `subject`, **`allow_text:
   false`**. The subject names every part AND its fine sub-structures (Step-0 grounding) so the art is dense,
   not cartoonish, and says *"centred with WIDE white margins for labels."* This crisp textless image is the
   base you keep.
2. **Labelled ORACLE `L` — an EDIT of `T` (not a fresh generation!):** `generate_artwork` with
   **`reference_images=[T]`, `conditioning: "layout"`, `allow_text: true`**, and a prompt like
   *"Reproduce this EXACT image unchanged, and ADD a clean text label in the white margin outside the subject
   for each of these parts: <list> — each connected by a thin straight leader line to the part. Do not alter
   the artwork; only add labels + leaders."* Because `L` is `T` + annotations, the anatomy is pixel-aligned.
   > **This MUST be an edit of `T`. A fresh `generate_artwork` (no `reference_images`) is a DIFFERENT
   > picture — its coordinates won't line up with `T`, and labels land at random. This is the #1 failure.**
3. **Read the placements** (`read_labels_from_image`, `image=L`, `base=T`, `structures=<list>`): reads each drawn label
   → its text, the pointer `target` (leader tip on the structure, snapped onto `T`'s ink), and `at` (where
   the label sits — kept in the margin where the model put it). Returns ready-to-draw `annotation`
   `elements`. (It reads DRAWN leaders, so positions are correct by construction.) Discard `L`.
4. **Draw the editable layer** (`render_editable_overlay`, `width`/`height` = `T`'s size, the `elements` from
   read_labels_from_image, `out_svg` + `out_png`) → editable `<text>` + leader/arrow per label, positioned by Nano
   Banana, over transparent.
5. **Composite → the deliverable** (`compose_figure_layers` with **`artwork=T`** (the textless PNG), **`overlay_svg_path`**
   = the `out_svg` render_editable_overlay just wrote, plus your `out_svg` + `out_png`) → **a layered editable SVG**
   (the textless art as `<image>` + live editable `<text>`/`<path>` labels/arrows) plus a flattened PNG to
   LOOK at. **This SVG is the agent's final figure.** For PowerPoint, `export_pptx(artwork=T,
   elements=<the read_labels_from_image elements>)`; for vector PDF, `export_pdf`.
6. **LOOK** at the flattened PNG. If a leader is mis-aimed or a label overlaps, re-run read_labels_from_image/render
   (or, if the art/labels themselves are wrong, replace `T` and its oracle) — never hand-edit coordinates.

> Labels sit in the margin because the ORACLE put them there and we keep `at`. The base `T` is textless, so
> the final SVG's `<image>` is always the full clean artwork — the image can never go missing.

## Step 2B — Composite (illustrated scene + illustrated flowchart)

The FigureLabs "anatomy + process, with icons not boxes" figure. Do 2A for the scene, and for the
flowchart:

1. **Icons — generate them in ONE call, not one-per-step (cost + cohesion):** call `generate_artwork`
   once with `template="process-icon"` and a `subject` that describes **all the step icons as a single
   clean grid/contact-sheet on white** (e.g. "a row of N simple flat icons, evenly spaced, one per cell:
   (1) …, (2) …, (3) …", with the shared `palette`). One Nano Banana image holds the whole set, so they
   come out perfectly consistent AND you pay for one generation, not N. Then crop the cells (the `read`
   tool can show you the sheet; use a quick `exec` PIL crop, or place the whole sheet and position node
   boxes over its cells). Only fall back to per-step calls if a step needs a wildly different icon.
   **Alternative (fewest calls, most cohesive):** generate the **entire flowchart strip as one textless
   illustrated panel** (all boxes + icons + connector arrows, NO text) in a single call, then place the
   editable `node` boxes / labels over its cells **by GEOMETRY** (you laid the strip out, so you know the
   cell positions) — do not try to locate them by vision.
2. **Layout:** `layout_flowchart` with one node per step (give `w`/`h`; omit `x`/`y` to auto-layer, or
   place them) + the edges between steps → node boxes + routed `edges[].points`.
3. **Draw:** `render_editable_overlay` with a **`node`** element per step (`x/y/w/h` from layout_flowchart, `text`
   = the step label, `icon` = that step's icon PNG, `step` = the number, `stroke` = a palette colour)
   and an **`arrow`** per edge (`points` from layout_flowchart). Add a `panel` frame + title if it helps.
4. **Composite** the scene-overlay and the flowchart-overlay over the artwork canvas (or compose each
   panel and place side by side). Keep ONE palette, ONE line weight, ONE icon style across both — that
   cohesion is what makes it read as a single professional figure.

## Step 2C — Structured (purely logical)

Write the diagram as PlantUML (follow **create-diagram**) → `plantuml` → PNG → **LOOK** and fix the
source. Use this only for a _non-illustrated_ logical diagram; for an illustrated flowchart prefer the
`node`-based editable flowchart in 2B (it's editable and style-matched, unlike a PlantUML raster).

## Step 2D — Illustrated (standalone, few/no labels)

`generate_artwork` with a template (set `allow_text: true` only if you truly want baked text) →
`verify_figure` → regenerate/refine if wrong. Ship PNG or run it through export.

### Accuracy path — condition on a sketch, reference, or template exemplars (highest trust)

Free text-to-image _invents_ structure. To get **correct** artwork, supply the structure and let the
model only paint it:

- Ask the user for a **rough sketch** or a **verified reference figure** for high-stakes/medical work.
- **No user reference? Find one:** `find_reference_image` (keyless web search) → **look at the
  candidates and pick the cleanest, most accurate** (prefer a clear labelled schematic over a
  cluttered/watermarked photo). **Always search in ENGLISH.** Then condition on it.
- Call `generate_artwork` with `reference_images: [<sketch/ref>]` and `conditioning`: `sketch`
  (rough layout to clean up — highest accuracy) / `layout` (preserve structure/connectivity exactly) /
  `style` (match look only).
- **Template exemplars** do the same for _style_: if a template has exemplar images, they're passed
  automatically as `conditioning: style`, so the look is repeatable. To pin a style, drop 1–2 gold
  images in the template's folder and list them under `exemplars` (see `templates/README.md`).

## Step 3 — Verify (the correctness gate)

- **Always run `verify_figure`** — don't trust your own eyes alone (some brains can't see the images
  tools return). It returns a TEXT verdict you can always read. Treat `{ok, missing, extra, wrong}` as
  ground truth and fix accordingly.
- `validate_svg` on the final SVG to confirm every label, arrow, and node is present and editable.
- It's a **visual/structural** check, not domain fact-checking — for medical/published work, still get
  a human nod.

## Step 4 — Export (the editable deliverables)

- The layered SVG from `compose_figure_layers` is already the editable, "vectorized" deliverable.
- `export_pptx` → artwork picture + **editable text boxes, rounded pill labels, connector arrows with
  real arrowheads, flowchart node boxes with icon pictures + step badges** — all retypable /
  recolourable / resizable in PowerPoint. Pass the same overlay `elements`.
- `export_pdf` → vector PDF (selectable text) for publication.

## Editability — set expectations, then pick the route

| Output                                   | Labels / leaders / arrows / flowchart nodes                                | The painted artwork                                                                                   |
| ---------------------------------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| **Native-label layered SVG/PPTX (default)** | ✅ each is its own vector object — move, resize, retype, recolour, reshape | ⚠️ one embedded **raster** image: scale/crop/replace as a whole, but individual structures are pixels |
| **Fully-vectorized SVG** (`trace_image`) | ✅ vector                                                                  | ✅ vector paths too — but **traced**, so many anonymous fill-paths, not clean named organelles        |
| **Structured / code route**              | ✅ vector                                                                  | ✅ born as clean, named vector shapes — but a flatter schematic look                                  |

> **Default = the layered SVG/PPTX.** It is already "vectorized enough" (editable labels/arrows/nodes
> over the raster). Only reach for `trace_image` when the user explicitly asks for the **painted
> artwork itself** to be editable shapes (see **vectorize-figure**) — tracing is lossy.

## Accuracy & cohesion principles (keep these in front of you)

- **Textless artwork + vector labels/nodes** removes the entire garbled-label error class. Never bake text.
- **One template, one palette, one line weight** across the scene, the icons, the flowchart, and the
  overlay — that single style system is what makes it read as a professional figure, not a collage.
- **Supply structure** for correctness: sketch/reference/exemplar beats free generation.
- **Labels at the right depth** — concise names for a parts/anatomy diagram; a short descriptor only
  where it aids an explanatory figure's goal (Step 0). Don't bloat, don't truncate — match the ask.
- **Draft → verify → human.** Position the figure as a first draft to check, not an oracle.
- **Look before you ship.** Preview artwork and composite; never export a figure you haven't viewed.
