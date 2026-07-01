"""vision plugin — the figure pipeline's "eye" (image analysis) as TOOLS.

Two tools:
  • extract_anchors — locate named structures -> label-anchor JSON for the overlay.
  • verify_figure   — VLM judge: does the figure contain exactly the expected structures, correctly?

Both call a Gemini vision model directly via google-genai (GEMINI_API_KEY/GOOGLE_API_KEY) and are
INDEPENDENT of the agent's reasoning model — so they work even when the brain is text-only
(e.g. DeepSeek) and can't see images itself. No core change, no new credential.
"""

from __future__ import annotations

from extract_anchors_tool import ExtractAnchorsTool
from verify_figure_tool import VerifyFigureTool


def register(api, ctx):
    api.register_tool(ExtractAnchorsTool(ctx.config))
    api.register_tool(VerifyFigureTool(ctx.config))
