"""vectorize plugin — turning raster figures into vector, two very different ways.

  • extract_annotations — SEMANTIC: pixel-diff a LABELLED figure against its textless base ->
    OCR'd editable `label`s + `arrow`/`leader` elements with the model's own geometry (the new
    default vectorize route for figures we generated). Deterministic; positions come from pixels.
  • trace_image — GEOMETRIC: color regions -> Bezier paths (logos / clean line-art). Needs a
    vtracer backend; errors actionably if absent. Lossy; opt-in only.

The VLM fallback for labelled figures (when extraction reports unanchored labels or there is no
usable base) is `read_labels_from_image` in the vision plugin.
"""

from __future__ import annotations

from extract_annotations_tool import ExtractAnnotationsTool
from trace_image_tool import TraceImageTool


def register(api, ctx):
    api.register_tool(ExtractAnnotationsTool(ctx.config))
    api.register_tool(TraceImageTool(ctx.config))
