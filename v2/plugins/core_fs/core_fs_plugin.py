"""Built-in 'core_fs' bundle — the filesystem tools (read / write / edit / ls / find / grep).

Migrated out of agentd core. Config-only (each tool takes ``ctx.config``); always registered.
Each tool self-describes via its own ``description`` (OpenClaw model — no separate card files).
"""

from __future__ import annotations

from fs_tools import EditTool, FindTool, LsTool, ReadTool, WriteTool
from grep_tool import GrepTool
from model_image_shrinker import ModelImageShrinker


def register(api, ctx):
    cfg = ctx.config
    # `read` makes images the size a model sees before they enter the conversation.
    api.register_tool(ReadTool(cfg, ModelImageShrinker()))
    for tool_cls in (WriteTool, EditTool, LsTool, FindTool, GrepTool):
        api.register_tool(tool_cls(cfg))
