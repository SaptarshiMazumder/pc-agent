---
name: create-scientific-figure
description: Create a publication-grade scientific figure from a prompt, paper/PDF, sketch, or reference image. Routes to the right machine — a structured diagram (flow/pathway/ER/network), an illustrated scene (anatomy/cells/physical setup), or the flagship HYBRID (illustrated artwork + an editable vector layer of labels, leaders, and arrows — the BioRender / Cell-journal look). Produces editable SVG / PPTX / vector PDF / PNG.
---

# Create a scientific figure

Turn a subject into a finished figure. The craft is **picking the right machine** and, for labelled
scientific figures, building the **hybrid**: rich artwork with the labels and arrows added as an
**editable vector layer** — not baked into pixels. That separation is what gives both the look _and_
correctness/editability.

You generate all content from the subject every time — never reuse example wording or a previous
figure's labels.

## The tools (each does ONE job)

| tool                         | does                                                                                                                                                                                                                                                                                                     |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `generate_artwork`           | text → raster illustration (Gemini 3 Pro Image / Nano Banana Pro). Default = **textless** and **richly shaded** (`style` default `biorender-3d`; also `cell-journal`, `watercolor-medical`, `flat-vector`). Optional reference images condition layout/style.                                            |
| `find_reference_image`       | keyless web image search → downloads candidate reference images and returns them to **look at & pick**; or downloads a specific `url`. Feed the chosen path to `generate_artwork` `reference_images` for accuracy grounding.                                                                                |
| `extract_anchors`            | finished image + structure names → `{label, target[x,y], box[x,y,w,h], side}` JSON (where to point each label + the structure's footprint).                                                                                                                                                              |
| `place_labels`               | anchors → non-overlapping label callouts placed **in a ring around each structure** (`strategy` default `auto`; pass the **artwork** PNG so labels avoid the drawing, and the anchors' `box` so they sit just outside each structure). `adjacent` = never spill; `callout` = classic two-column margins. |
| `route_graph`                | nodes + edges → routed connector waypoints (for flows/arrows between elements).                                                                                                                                                                                                                          |
| `render_overlay`             | high-level spec → **editable** SVG: labels, boxed/pill labels, leader callouts, premium arrows (`style` clean/biorender/subtle; tapered/curved/gradient/glow). Leaders can end in a dot **or** an arrowhead (`head`).                                                                                    |
| `compose_layers`             | artwork + overlay → flattened PNG **and** layered editable SVG (artwork `<image>` + live `<text>`/`<path>`).                                                                                                                                                                                             |
| `verify_figure`              | image + expected structures → `{ok, missing, extra, wrong, notes}` (the correctness gate).                                                                                                                                                                                                               |
| `validate_svg`               | parse + inventory an SVG (labels/arrows/embedded image).                                                                                                                                                                                                                                                 |
| `plantuml`                   | diagram code → PNG, for the structured path. See **create-diagram**.                                                                                                                                                                                                                                     |
| `export_pptx` / `export_pdf` | editable PowerPoint / vector PDF.                                                                                                                                                                                                                                                                        |

**One rule that makes everything line up:** the overlay is authored in the **artwork's pixel
coordinate space** (origin top-left) — exactly what `extract_anchors` and `route_graph` return. Keep
the overlay `width`/`height` equal to the artwork's.

## Step 0 — Understand & refine the brief (gated)

- **Read the source** (prompt, and ingest a paper/PDF or reference image if given). Pull out the
  entities, their relationships, the labels, and the intended style. If the details aren't clear, You may do as much research as you need by searching the web. Gather all the information you need, in order to fullfil the user's request. Don't just rely on the user's message, but understand the INTENT and the GOAL (IMPORTANT).
- **Gate the refinement by complexity:**
  - Simple, one-object prompt ("draw a phospholipid bilayer") → skip ahead, generate.
  - Multi-entity / pathway / "publication-ready" → write a short **Figure Spec** (entities, relations,
    labels, layout, style) and work from it.
- **Ask 1–3 questions ONLY when the science depends on it** (e.g. "do the re-infection arrows loop
  back to memory or naïve B cells?"). Otherwise pick a sensible default and proceed.
- For high-stakes/medical figures, ask for a **sketch or reference image** — the most reliable way to
  get the structure right (the human owns the ground truth; the model only styles it).

## Step 1 — Route to a machine

| If the figure is…                                                            | Route           | Why                                                    |
| ---------------------------------------------------------------------------- | --------------- | ------------------------------------------------------ |
| logical: flow, hierarchy, pathway-as-boxes, ER, network                      | **Structured**  | a layout algorithm makes it exact                      |
| a rich painted scene with **no/few labels**                                  | **Illustrated** | one generation, fast                                   |
| painted artwork **with labels / leaders / arrows** (most scientific figures) | **Hybrid**      | the look from art, correctness from the vector overlay |

## Step 2A — Structured

1. Write the diagram as PlantUML (follow **create-diagram**). 2. `plantuml` → PNG. 3. **LOOK** at it;
   fix the source if a box is clipped or a relation is wrong. Verify the _content_ (right nodes/edges) —
   the geometry is guaranteed.

## Step 2B — Illustrated (standalone, few/no labels)

1. `generate_artwork` with a clear style prompt (set `allow_text: true` only if you truly want baked
   text). **Fit the whole subject in frame** — the entire illustration inside the image bounds with a
   small margin, nothing cropped at the edges, unless it's an inherently continuous/tiling subject
   meant to span edge-to-edge. 2. `verify_figure` against what it should show. 3. Regenerate or refine
   if it's wrong. Ship PNG or run it through export.

## Step 2C — Hybrid (the flagship)

1. **Artwork:** `generate_artwork` — describe the scene, keep it **textless** (the default). Leave
   `style` at `biorender-3d` for a **richly shaded, volumetric** look (only drop to `flat-vector` if a
   flat schematic is wanted). Keep it textless AND **arrow-less** — process arrows (flow, injection,
   diameter) are drawn as vector overlay in step 5, never baked into the raster. **Fit the whole
   subject in frame:** compose so the entire illustration sits inside the image bounds with a small
   margin — nothing cropped or bleeding off the edges — UNLESS the subject is inherently
   continuous/tiling (e.g. a membrane bilayer or tissue field meant to span edge-to-edge). For accuracy,
   pass a sketch/reference via `reference_images` (+ `conditioning` — see the accuracy box below). Note
   the output pixel size.
2. **Locate structures:** `extract_anchors` with the artwork + the list of structures to label →
   anchors (`target` point + `box` footprint + `side`).
3. **Place the labels:** `place_labels` with the artwork width/height, the anchors, **and the artwork
   PNG (`artwork`)**. Leave `strategy` at `auto` so each label is placed **right next to its
   structure** in real whitespace (the artwork mask + each anchor's `box` keep labels off the drawing),
   spilling to a margin only when crowded. **Don't hand-place labels**, and **don't default to the
   two-column `callout`** — reserve that for a dense central illustration framed by clear side margins.
4. **Route flows** (only if arrows connect elements, e.g. a pathway): `route_graph` with the element
   boxes/positions → `edges[].points`.
5. **Draw the editable layer:** `render_overlay` at the artwork's width/height. Pass the `elements`
   from `place_labels`, plus:
   - `arrow` elements for every process/flow (feed `route_graph`'s `points`; default `style` `clean`
     or `biorender` gives a weighty soft-headed arrow — **never leave a process as a thin plain
     line**, and never rely on a baked-in arrow). Add gradient/glow for emphasis.
   - `label`/`panel` for the title and region frames.
     Write both `out_svg` and `out_png`.
6. **Composite:** `compose_layers` (artwork + overlay) → `out_png` (flattened) **and** `out_svg`
   (layered editable).
7. **LOOK** at the composite (it's returned as an image). Fix mis-aimed leaders or crossing arrows by
   adjusting the spec and re-running 5–6 (re-run `place_labels` with a bigger `gap`/`inset` if labels
   feel cramped).

### Accuracy path — condition on a sketch or reference (highest trust)

Free text-to-image _invents_ structure (mislabelled vessels, wrong counts, impossible anatomy). To
get **correct** artwork, supply the structure and let the model only paint it:

- Ask the user for a **rough sketch** or a **verified reference figure** when the figure is
  high-stakes (medical/published) or the layout matters.
- **No user reference? Find one.** If the subject has well-known public references (a known organism,
  organ, apparatus, molecule), call `find_reference_image` (keyless web image search), **look at the
  candidates it returns, and pick the cleanest, most accurate one** — prefer a clear labelled
  diagram/schematic over a cluttered photo or a **watermarked** stock image. (You can also find a URL
  via `web_search`/Gemini/browser and pass it to `find_reference_image` as `url` to download it.)
  **Always search in ENGLISH** — whatever language the user wrote in, translate the subject to English
  for the query; English returns the best reference results. Then condition on it. Say you used a web
  reference.
- Call `generate_artwork` with `reference_images: [<sketch/ref>]` and `conditioning`:
  - `sketch` — the ref is a rough layout to clean up (keeps arrangement, polishes the look). Highest accuracy.
  - `layout` — preserve the reference's structure/connectivity exactly; restyle only.
  - `style` — match the look only (layout may differ).
- Then continue the hybrid flow (anchors → place_labels → overlay → composite). The human owns the
  ground truth; the model only styles it — this is the most trustworthy route to a correct figure.

## Step 3 — Verify (the correctness gate)

- **Always run `verify_figure` — don't trust your own eyes alone.** Some brains can't actually see the
  images tools return (e.g. a DeepSeek brain: litellm does not forward images to it). `verify_figure`
  (Gemini) returns a TEXT verdict you can always read, so it — not your own perception — is the
  authoritative correctness check. Treat its `{ok, missing, extra, wrong}` as ground truth and fix
  accordingly, even if a render "looked fine" to you.
- `verify_figure` with the image + the expected structures/relationships. If `ok: false`, fix the
  flagged `missing` / `extra` / `wrong` and re-render. Remember it's a **visual/structural** check,
  not domain fact-checking — for medical/published work, still get a human nod.
- `validate_svg` on the final SVG to confirm every label and arrow is present and editable.

## Step 4 — Export

- The layered SVG from `compose_layers` is already the editable, "vectorized" deliverable.
- `export_pptx` → artwork + editable text boxes + connector arrows (pass the same overlay elements).
- `export_pdf` → vector PDF (selectable text) for publication.

## Editability — set expectations, then pick the route

Be explicit with the user about **what they can edit afterwards** — it decides the route:

| Output                                                      | Labels / leaders / arrows                                                  | The painted artwork                                                                                                        |
| ----------------------------------------------------------- | -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Hybrid layered SVG/PPTX (default)**                       | ✅ each is its own vector object — move, resize, retype, recolour, reshape | ⚠️ one embedded **raster** image: scale/crop/replace as a whole, but individual structures are pixels, not separate shapes |
| **Fully-vectorized SVG** (FigureLabs "AI Vectorizer" style) | ✅ vector                                                                  | ✅ vector paths too — but **traced**, so many anonymous fill-paths, not clean named organelles                             |
| **Structured / code route**                                 | ✅ vector                                                                  | ✅ born as clean, named vector shapes — but a flatter schematic look, not painted realism                                  |

> **Default = the layered SVG. Do NOT trace the artwork on your own.** The Hybrid layered SVG/PPTX
> above is the normal deliverable and is already "vectorized enough" (editable labels/arrows over the
> raster). **Only** reach for `trace_image` / all-vector output when the user **explicitly asks for the
> painted artwork itself to be vector/editable shapes** (e.g. "vectorize the whole image", "make the
> shapes editable in Illustrator"). Tracing is **lossy**: it produces many anonymous color-blob paths,
> huge files, and a slightly degraded look — so it's opt-in, never a silent default.

- **To make the ARTWORK itself editable — ONLY on explicit
  request:** run `trace_image` on the generated artwork → a traced SVG, then `compose_layers` with
  **`artwork_svg_path`** (instead of the raster `artwork`) + the overlay → an **all-vector** SVG where
  every artwork shape _and_ every label is editable. Trade-off to state up front: traced shapes are
  editable but are many anonymous fill-paths, not tidy named organelles (uses the vtracer backend).
- **To RESHAPE a structure cleanly:** don't node-edit traced paths — **regenerate that region** with
  `generate_artwork` (+ `conditioning`/reference), exactly like FigureLabs' "redraw the parts you
  select". Then re-composite.
- **If the user needs every structure as a clean editable object:** use the **structured route**
  (vector by construction) — or a hybrid where the must-edit structures are vector-drawn and only the
  texture comes from diffusion.

## Accuracy principles (keep these in front of you)

- **Textless artwork + vector labels** removes the entire garbled-label error class. Never bake text.
- **Supply structure** for correctness: sketch-to-figure or a reference image beats free generation.
- **Draft → verify → human.** Position the figure as a first draft to check, not an oracle.
- **Look before you ship.** Preview artwork and composite; never export a figure you haven't viewed.
