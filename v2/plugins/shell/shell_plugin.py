"""Built-in 'shell' bundle — the command tools (exec / process).

Migrated out of agentd core. Config-only; always registered. ``exec``/``process`` self-limit
(own timeouts) so the guard wrapper doesn't double-time them — see their default_* policy attrs.
"""

from __future__ import annotations

from exec_tool import ExecTool, ProcessTool


def register(api, ctx):
    api.register_tool(ExecTool(ctx.config))
    api.register_tool(ProcessTool(ctx.config))
