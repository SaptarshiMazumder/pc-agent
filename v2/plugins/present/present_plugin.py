"""Built-in 'present' bundle — the universal present_files deliverable tool.

Always registered (no gating): every agent should be able to present a file it produced.
Producing tools declare their own outputs; this covers everything else.
"""

from __future__ import annotations


def register(api, ctx):
    from present_tool import PresentFilesTool

    api.register_tool(PresentFilesTool(ctx.config))
