"""slides plugin — generate slide-deck files (one tool: `make_pptx`).

Home for real slide formats (PowerPoint now; Google Slides / Keynote could join as sibling tools).
"""

from __future__ import annotations

from pptx_tool import MakePptxTool


def register(api, ctx):
    api.register_tool(MakePptxTool(ctx.config))
