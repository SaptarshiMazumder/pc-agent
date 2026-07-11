"""vectorize plugin — turning raster figures into vector.

  • figure_to_svg — ★ ONE-CALL, self-contained labelled-figure -> editable layered SVG (strip ->
    OCR text -> semantic+blob arrows -> raster/vector artwork -> composed SVG). Stateless: any
    agent can call it in any flow/chat with just an image path. The "make this editable" tool.
  • extract_annotations — the lower-level SEMANTIC extractor (diff -> OCR labels + skeleton
    arrows as overlay elements); use it when you want the elements spec to edit before composing.
  • trace_image — GEOMETRIC: color regions -> Bezier paths (logos / clean line-art). Lossy; opt-in.

The VLM fallback for labelled figures (unanchored labels, or no usable base) is
`read_labels_from_image` in the vision plugin.
"""

from __future__ import annotations

from extract_annotations_tool import ExtractAnnotationsTool
from figure_to_svg_tool import FigureToSvgTool
from trace_image_tool import TraceImageTool


def register(api, ctx):
    api.register_tool(FigureToSvgTool(ctx.config))
    api.register_tool(ExtractAnnotationsTool(ctx.config))
    api.register_tool(TraceImageTool(ctx.config))
