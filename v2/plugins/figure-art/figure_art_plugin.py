"""figure-art plugin — raster artwork generation + targeted editing via Gemini (Nano Banana).

Tools: `generate_artwork` (illustration for the hybrid overlay flow), `edit_artwork` (targeted
"change ONLY this" edit of an existing image — the region-redraw fix path), plus template browsing
and reference-image search. Generation goes through the runtime's model funnel (`self.models` /
oneshot.generate_image) — mode-correct in BYOK/cloud/sandbox with no credential held here.
Brain-agnostic: works whether the agent's reasoning model is Gemini, DeepSeek, or other.
"""

from __future__ import annotations

from edit_artwork_tool import EditArtworkTool
from find_reference_image_tool import FindReferenceImageTool
from generate_artwork_tool import GenerateArtworkTool
from list_templates_tool import ListTemplatesTool


def register(api, ctx):
    api.register_tool(GenerateArtworkTool(ctx.config))
    api.register_tool(EditArtworkTool(ctx.config))
    api.register_tool(FindReferenceImageTool(ctx.config))
    api.register_tool(ListTemplatesTool(ctx.config))
