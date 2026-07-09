---
name: vectorize-figure
description: Make a figure editable ON DEMAND. For a figure this pipeline generated (or any labelled raster), extract_annotations pixel-diffs it against a textless base (auto-stripped) and rebuilds every label/leader/arrow as real editable vector text/paths with the model's exact geometry; read_labels_from_image is the VLM fallback. Or geometrically trace shapes to paths. Use when the user asks to convert/edit a figure as SVG, or clicks Edit-as-SVG.
---

# Vectorize a figure

Turn a **flat raster figure** into something editable. Three approaches — pick by what you have:

| Have | Approach | Result |
| --- | --- | --- |
| a figure WE generated (labelled PNG, manifest exists) | ★ `extract_annotations` | pixel-exact: OCR'd editable `<text>` + `arrow`/`leader` paths with the model's own curves, widths, colours, arrowheads |
| any labelled raster (no base, extraction fails/unanchored) | `read_labels_from_image` | VLM-read labels + leader endpoints → `annotation` elements |
| a logo / clean line-art, user wants the SHAPES as vectors | `trace_image` | regions → Bezier paths (NOT editable text; lossy; opt-in) |

Most requests ("make this editable / let me fix the labels") mean the **first row that applies**.

## Workflow — extraction (the default)

1. **Read the figure's manifest** if it exists (`<figure>_manifest.json`) — it names the labelled
   image and any existing base. No manifest? The labelled image the user pointed at is the input.
2. **`extract_annotations(labeled=L)`** (pass `base=` only if a pixel-aligned textless base already
   exists — e.g. from the manifest). It strips a base `T'`, diffs, OCRs, traces, and writes the
   spec JSON. **Heed its gates:**
   - ALIGNMENT warning → LOOK at `T'`; if the strip altered artwork, re-run (a fresh strip).
   - UNANCHORED labels → the model drew them with no leader: add those few via
     `read_labels_from_image(image=L, base=T', structures=[…])`, or accept them as floating text.
3. **LOOK at `T'`** — labels removed cleanly, art intact?
4. **`render_editable_overlay(elements_path=<the spec JSON>, out_svg, out_png)`** — never retype
   elements. LOOK at the PNG.
5. **`compose_figure_layers(artwork=T', overlay_svg_path=…, out_svg, out_png)`** → the layered
   editable SVG + flattened PNG. `validate_svg`. The composite must look like the ORIGINAL — that
   is the WYSIWYG contract of extraction.
6. **Update the manifest**: `state: "vectorized"`, record `base`/`elements`/`overlay`/`final_svg`,
   bump `version`. `export_pptx(artwork=T', elements_path=…)` / `export_pdf` on request.

> Fixing one wrong label/pointer afterwards = edit that element in the spec JSON and re-run
> render + compose — never paint over the raster, never re-bake text.

## Workflow — VLM fallback (`read_labels_from_image`)

For an arbitrary labelled image where extraction isn't usable end-to-end:
1. `read_labels_from_image` with just the `image` — it strips a clean textless `base_png` and reads
   every label → `annotation` elements (text + pointer + label position).
2. LOOK at `base_png`, then the same render → compose → validate → export chain as above.

## Workflow — geometric tracing (shapes only) — OPT-IN

**Only when the user explicitly asks to vectorize the shapes/artwork itself** ("vectorize the whole
image", "make the shapes editable"). It is **lossy** (color-blob paths, big files) — never a
default. `trace_image` (`mode` color|binary, `precision` 1-8); if the backend is missing it returns
an install message — relay it, don't pretend it traced.
