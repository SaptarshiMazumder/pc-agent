"""figure-art plugin — raster artwork generation via Gemini (Nano Banana).

One tool today: `generate_artwork` (textless illustration for the hybrid overlay flow). Uses the
google-genai SDK directly with the existing GEMINI_API_KEY / GOOGLE_API_KEY — no core change, no new
credential. Brain-agnostic: works whether the agent's reasoning model is Gemini, DeepSeek, or other.
"""

from __future__ import annotations

from generate_artwork_tool import GenerateArtworkTool
from find_reference_image_tool import FindReferenceImageTool
from list_templates_tool import ListTemplatesTool


def register(api, ctx):
    api.register_tool(GenerateArtworkTool(ctx.config))
    api.register_tool(FindReferenceImageTool(ctx.config))
    api.register_tool(ListTemplatesTool(ctx.config))
