"""vectorize plugin — turn flat figure images into editable vectors.

  • figure_to_svg        — ONE-CALL: any labelled figure PNG -> editable layered SVG (text +
                           arrows as real vector elements over clean artwork). The canvas
                           "Convert to vector" button invokes exactly this.
  • extract_annotations  — the annotation-extraction stage alone (labels/arrows -> overlay JSON).
  • trace_image          — GEOMETRIC: color regions -> Bezier paths (logos / clean line-art).
                           Needs a vtracer backend; errors actionably if absent.

NOTE: the old semantic "reconstruct_svg" vectorizer was removed: it painted white over detected
text to avoid double labels, which white-outs artwork when labels aren't purely on a white margin.
"""

from __future__ import annotations

from extract_annotations_tool import ExtractAnnotationsTool
from figure_to_svg_tool import FigureToSvgTool
from trace_image_tool import TraceImageTool


def register(api, ctx):
    api.register_tool(FigureToSvgTool(ctx.config))
    api.register_tool(ExtractAnnotationsTool(ctx.config))
    api.register_tool(TraceImageTool(ctx.config))
