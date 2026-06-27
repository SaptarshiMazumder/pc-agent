"""Built-in 'core_fs' bundle — the filesystem tools (read / write / edit / ls / find).

Migrated out of agentd core. Config-only (each tool takes ``ctx.config``); always registered.
Each tool self-describes via its own ``description`` (OpenClaw model — no separate card files).
"""

from __future__ import annotations

from fs_tools import EditTool, FindTool, LsTool, ReadTool, WriteTool


def register(api, ctx):
    cfg = ctx.config
    for tool_cls in (ReadTool, WriteTool, EditTool, LsTool, FindTool):
        api.register_tool(tool_cls(cfg))
