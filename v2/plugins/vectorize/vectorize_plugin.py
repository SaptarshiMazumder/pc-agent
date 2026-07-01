"""vectorize plugin — turn an existing flat image into editable vectors.

  • reconstruct_svg — SEMANTIC: VLM rebuilds text labels + arrows as editable <text>/<path> over the
    artwork (the "AI Vectorizer"; uses Gemini + the figures overlay engine). No new dep.
  • trace_image    — GEOMETRIC: regions -> Bezier paths (logos/line-art). Needs a vtracer backend;
    errors actionably if absent.

reconstruct_svg is the one that yields editable labels/arrows; trace_image is for shape vectorizing.
"""

from __future__ import annotations

from reconstruct_svg_tool import ReconstructSvgTool
from trace_image_tool import TraceImageTool


def register(api, ctx):
    api.register_tool(ReconstructSvgTool(ctx.config))
    api.register_tool(TraceImageTool(ctx.config))
