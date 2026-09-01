---
name: vectorize-figure
description: Make an EXISTING flat figure image (PNG/JPG/screenshot) editable. Reads its text labels + leaders and rebuilds them as real editable vector text/arrows over a CLEAN textless copy of the artwork (labels retypable, arrows recolourable) — or geometrically traces shapes to paths. Use when the user hands you a finished raster figure and wants an editable SVG/PPTX.
---

# Vectorize a figure

Turn a **flat raster figure** into something editable. Two different meanings of "vectorize":

| Want | Approach | Result |
| --- | --- | --- |
| editable **text labels & arrows** | `read_labels_from_image` → `render_editable_overlay` → `compose_figure_layers` | labels become real `<text>`; arrows become `<path>` — over a clean textless copy of the art |
| scalable **shapes** (logo, clean line-art) | `trace_image` | regions → Bezier paths (NOT editable text) |

Most requests ("make this figure editable / let me fix the labels") mean the **first row**.

## Workflow — editable labels & arrows

1. **`read_labels_from_image` with just the `image`** (no `base`). It strips baked labels + leaders off the image → a clean **textless base** (`base_png`), and reads every label → ready-to-draw `annotation` `elements`.
2. **LOOK** — `read` the `base_png` to confirm the labels were removed cleanly.
3. **Draw the editable layer** — `render_editable_overlay` with the `elements`.
4. **Composite** — `compose_figure_layers` with `artwork=base_png` and `overlay_svg_path` = overlay's `out_svg`.
5. **Verify & export** — `validate_svg` confirms the editable text/arrows are present; `export_pptx` for PowerPoint, `export_pdf` for print.

> If a label reads wrong or a pointer is off, edit that one `element` and re-run — never paint over the raster.

## Workflow — geometric tracing (shapes only) — OPT-IN

Only use `trace_image` when the user explicitly asks to vectorize the shapes/artwork. It is **lossy** — color-blob paths, large files, degraded look.
