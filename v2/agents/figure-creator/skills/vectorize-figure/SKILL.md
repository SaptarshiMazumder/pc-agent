---
name: vectorize-figure
description: Make an EXISTING flat figure image (PNG/JPG/screenshot) editable. Reads its text labels + leaders and rebuilds them as real editable vector text/arrows over a CLEAN textless copy of the artwork (labels retypable, arrows recolourable) — or geometrically traces shapes to paths. Use when the user hands you a finished raster figure and wants an editable SVG/PPTX.
---

# Vectorize a figure

Turn a **flat raster figure** into something editable. Two different meanings of "vectorize" — pick the
one the user actually needs:

| Want | Approach | Result |
| --- | --- | --- |
| editable **text labels & arrows** | `read_labels_from_image` → `render_editable_overlay` → `compose_figure_layers` | labels become real `<text>` you can retype; arrows become `<path>` you can recolour — over a clean textless copy of the art |
| scalable **shapes** (logo, clean line-art) | `trace_image` | regions → Bezier paths (NOT editable text) |

Most requests ("make this figure editable / let me fix the labels") mean the **first row**.

## Workflow — editable labels & arrows (the usual case)

The trick that avoids DOUBLE text (a new label on top of the baked one) and NEVER white-outs the art: we
overlay the editable labels onto a **clean textless copy** of the image, not the labelled original.

1. **`read_labels_from_image` with just the `image`** (no `base`). On any labelled image it will:
   - ask Gemini to **strip the baked labels + leaders** off the image → a clean **textless base** (returned
     as `base_png`), and
   - read every label → ready-to-draw `annotation` `elements` (text + pointer `target` + label position
     `at`), positioned in `base_png`'s coordinate space with pointers snapped onto the art.
   (If you already have a textless version, pass it as `base` and it skips the strip.)
2. **LOOK** — `read` the `base_png` to confirm the labels were removed cleanly and the art is intact.
3. **Draw the editable layer** — `render_editable_overlay` with the `elements`, `width`/`height` = `base_png`'s size,
   `out_svg` + `out_png`.
4. **Composite** — `compose_figure_layers` with **`artwork=base_png`** and **`overlay_svg_path`** = the overlay's
   `out_svg` → the layered editable SVG (clean art `<image>` + live `<text>`/`<path>`) + a flattened PNG.
5. **Verify & export** — `validate_svg` confirms the editable text/arrows are present; `export_pptx`
   (`artwork=base_png`, `elements`) for PowerPoint, `export_pdf` for print. The layered SVG is already the
   editable deliverable.

> If a label reads wrong or a pointer is off, edit that one `element` and re-run `render_editable_overlay` +
> `compose_figure_layers` — never paint over the raster, and never re-bake text.

## Workflow — geometric tracing (shapes only) — OPT-IN

**Only use `trace_image` when the user explicitly asks to vectorize the shapes/artwork** (e.g. "vectorize
the whole image", "make the shapes editable"). It is **lossy** — color-blob paths, large files, degraded
look — so it is never the default; for "make this editable" use the label workflow above.

1. `trace_image` (`mode`: color | binary; `precision` 1-8, default 6). Needs a vtracer backend; if missing
   it returns an actionable install message — relay it, don't pretend it traced.
2. Use for scalable shapes, NOT editable text (a label becomes letter-shaped outlines, not `<text>`).
