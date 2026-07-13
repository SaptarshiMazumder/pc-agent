"""Built-in 'computer' bundle — the computer-use dispatcher tool (screen/keyboard/mouse).

The computer PROVIDER (the OS driver) stays in agentd core as an injected dependency; this bundle
receives it via DI (``ctx.computer``) and registers the TOOL the model calls. Gating ported from
build_tools: no provider (computer disabled / unsupported OS) => nothing registers.
"""

from __future__ import annotations


def register(api, ctx):
    if ctx.computer is None:
        return
    from computer_tool import ComputerTool

    api.register_tool(ComputerTool(ctx.config, ctx.computer))
