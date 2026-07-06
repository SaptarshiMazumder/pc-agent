"""Built-in 'show' bundle — the universal show_files deliverable tool.

Always registered (no gating): every agent should be able to show a file it produced.
Producing tools declare their own outputs; this covers everything else.
"""

from __future__ import annotations


def register(api, ctx):
    from show_tool import ShowFilesTool

    api.register_tool(ShowFilesTool(ctx.config))
