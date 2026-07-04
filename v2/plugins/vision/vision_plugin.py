"""vision plugin — the figure pipeline's "eye" (image analysis) as TOOLS.

Two tools:
  • read_labels_from_image — READ the labels a model already DREW on a figure -> ready-to-draw overlay
    `annotation` elements (text + pointer + label position). Reliable: it traces DRAWN labels/leaders,
    not anatomy. Works in the create flow (pass a textless `base`) or standalone (strips labels via Gemini).
  • verify_figure — VLM judge: does the figure contain exactly the expected structures, correctly?

(The old `locate_structures_in_image` — which GUESSED where anatomy was and mis-placed labels — was removed.)

Both call a Gemini vision model directly via google-genai (GEMINI_API_KEY/GOOGLE_API_KEY) and are
INDEPENDENT of the agent's reasoning model — so they work even when the brain is text-only
(e.g. DeepSeek) and can't see images itself. No core change, no new credential.
"""

from __future__ import annotations

from read_labels_tool import ReadLabelsTool
from verify_figure_tool import VerifyFigureTool


def register(api, ctx):
    api.register_tool(ReadLabelsTool(ctx.config))
    api.register_tool(VerifyFigureTool(ctx.config))
