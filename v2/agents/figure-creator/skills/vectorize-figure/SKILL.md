---
name: vectorize-figure
description: Make an EXISTING flat figure image (PNG/JPG/screenshot) editable. Semantically rebuilds its text labels and arrows as real, editable vector objects layered over the artwork (the "AI Vectorizer"), or geometrically traces shapes to paths. Use when the user hands you a finished raster figure and wants to retype labels, recolour arrows, or export an editable SVG/PPTX.
---

# Vectorize a figure

Turn a **flat raster figure** into something editable. Two different meanings of "vectorize" — pick
the one the user actually needs:

| Want | Tool | Result |
| --- | --- | --- |
| editable **text labels & arrows** | `reconstruct_svg` | labels become real `<text>` you can retype; arrows become `<path>` you can recolour — layered over the original artwork |
| scalable **shapes** (logo, clean line-art) | `trace_image` | regions → Bezier paths (NOT editable text) |

Most requests ("make this figure editable / let me fix the labels") mean **`reconstruct_svg`**.

## Workflow — semantic reconstruction (the usual case)

1. `reconstruct_svg` with the `image` and an `out_svg`. A vision model reads every label and detects
   every arrow, then rebuilds them as editable vector over the artwork. It returns the discovered
   `elements` too.
2. **LOOK** at the result (`render_svg` the out_svg, or open it). Check the labels transcribed
   correctly and sit in the right place.
3. **Fix if needed:** the returned `elements` are `render_overlay`-compatible — adjust any wrong text
   or position and re-run `render_overlay` + `compose_layers` to rebuild the layer cleanly.
4. `validate_svg` to confirm the editable text/arrows are present.
5. Export: the layered SVG is already editable; `export_pptx` for PowerPoint, `export_pdf` for print.

### Honest limitation — say it to the user

The editable vector layer sits **on top of the original raster**, whose baked-in text still shows
underneath. So a *new* label will overlap the old one. For a clean swap you must remove the original
text first — either inpaint it out, or regenerate fresh **textless** artwork with `generate_artwork`
and place the reconstructed labels over that (see **create-scientific-figure**, hybrid path). Tell the
user which they want before promising "fully editable."

## Workflow — geometric tracing (shapes only) — OPT-IN

**Only use `trace_image` when the user explicitly asks to vectorize the shapes/artwork** (e.g.
"vectorize the whole image", "make the shapes editable"). It is **lossy** — color-blob paths, large
files, degraded look — so it is never the default; for "make this editable" prefer `reconstruct_svg`
(above). Good fits: logos, clean line drawings, or an explicit "trace the artwork into shapes" request.

1. `trace_image` (`mode`: color | binary; `precision` 1-8, default 6). It needs a vtracer backend; if
   missing it returns an actionable install message — relay that, don't pretend it traced.
2. Use for scalable shapes, NOT editable text (a label becomes letter-shaped outlines, not `<text>`).
