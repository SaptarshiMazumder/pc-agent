"""video plugin — assemble video from frames/clips + audio (one tool: `stitch_video`)."""

from __future__ import annotations

from stitch_tool import StitchVideoTool


def register(api, ctx):
    api.register_tool(StitchVideoTool(ctx.config))
