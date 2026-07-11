---
name: vectorize-figure
description: Make a figure editable ON DEMAND. For a figure this pipeline generated (or any labelled raster), extract_annotations pixel-diffs it against a textless base (auto-stripped) and rebuilds every label/leader/arrow as real editable vector text/paths with the model's exact geometry; read_labels_from_image is the VLM fallback. Or geometrically trace shapes to paths. Use when the user asks to convert/edit a figure as SVG, or clicks Edit-as-SVG.
---

# Vectorize a figure

Turn a **flat raster figure** into an editable SVG. Pick by what the user wants:

| Want | Tool | Result |
| --- | --- | --- |
| "make this editable / convert to SVG / vectorize this figure" | ★ **`figure_to_svg`** | ONE call → editable layered SVG: OCR'd `<text>`, semantic vector arrows (VLM-read, pixel-snapped) + blob-trace fallback, crisp raster artwork (or vector). |
| the SAME, but you want to hand-edit the element spec before composing | `extract_annotations` → `render_editable_overlay` → `compose_figure_layers` | the lower-level 3-step path; gives you the elements JSON to tweak |
| a plain logo / line-art, user wants the SHAPES as vectors (no labels) | `trace_image` | regions → Bezier paths (NOT editable text; lossy; opt-in) |

Most requests ("make this editable / let me fix the labels") are the **first row** — use
`figure_to_svg`.

## Workflow — the one-call converter (the default)

1. **`figure_to_svg(image=<the figure PNG>)`** — that's it. It is self-contained and stateless:
   strips a textless base, OCRs the text, rebuilds arrows semantically (with a blob-trace
   fallback), keeps the artwork as a crisp raster, and writes the editable SVG + a preview PNG.
   - Pass `base=` if you already have a pixel-aligned textless base (skips the strip).
   - `artwork_mode="vector"` to vtrace the artwork into editable shapes too (fully editable,
     figurelabs-style, softer look). Default `raster` = highest quality.
   - `semantic_arrows=false` to skip the VLM and blob-trace all arrows (faster, figurelabs-parity).
2. **LOOK** at the preview PNG — it must look like the original (WYSIWYG). If artwork was altered
   (a bad strip), pass a better `base` or re-run.
3. `validate_svg` the result; `export_pptx` / `export_pdf` on request.

## Workflow — element-level control (`extract_annotations`)

When you need to inspect or edit the elements before composing (e.g. rename a label, restyle an
arrow up front): `extract_annotations(labeled=L)` writes the spec JSON → edit it →
`render_editable_overlay(elements_path=…)` → `compose_figure_layers(artwork=base, overlay_svg_path=…)`.
Fixing one wrong label afterwards = edit that element and re-render — never paint over the raster.

## Workflow — VLM fallback (`read_labels_from_image`)

If OCR/extraction can't read a figure end-to-end (unusual fonts, no clean base):
`read_labels_from_image` with just the `image` strips a clean base and reads labels → `annotation`
elements → render → compose → validate → export.

## Workflow — geometric tracing (shapes only) — OPT-IN

**Only when the user explicitly asks to vectorize the shapes/artwork itself** ("vectorize the whole
image", "make the shapes editable"). Lossy (color-blob paths, big files) — never a default.
`trace_image` (`mode` color|binary, `precision` 1-8); relay its install message if the backend is
missing, don't pretend it traced.
