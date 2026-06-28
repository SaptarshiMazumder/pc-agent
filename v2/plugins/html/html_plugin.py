"""html plugin — render HTML/URL to an image or PDF (one tool: `render_html`)."""

from __future__ import annotations

from html_tool import RenderHtmlTool


def register(api, ctx):
    api.register_tool(RenderHtmlTool(ctx.config))
