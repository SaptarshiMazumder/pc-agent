"""tts plugin — text-to-speech (one tool: `tts`)."""

from __future__ import annotations

from tts_tool import TtsTool


def register(api, ctx):
    api.register_tool(TtsTool(ctx.config))
