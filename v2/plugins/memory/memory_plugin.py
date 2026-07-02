"""Built-in 'memory' bundle — long-term recall + write across sessions (remember / memory_search /
memory_get / memory_consolidate).

Gating ported from build_tools: registers only when memory is enabled AND a bank is wired (via DI,
``ctx.memory_bank``). OFF => the tools never exist (toolset unchanged).
"""

from __future__ import annotations


def register(api, ctx):
    if not (getattr(ctx.config, "memory_enabled", False) and ctx.memory_bank is not None):
        return
    from agentd.infrastructure.memory.background import BackgroundEmbedder
    from memory_tools import (
        MemoryConsolidateTool,
        MemoryGetTool,
        MemorySearchTool,
        RememberTool,
    )

    bank = ctx.memory_bank
    embedder = BackgroundEmbedder(bank)   # remember() writes now, embeds off the turn
    api.register_tool(RememberTool(bank, embedder))
    api.register_tool(MemorySearchTool(bank))
    api.register_tool(MemoryGetTool(bank))
    api.register_tool(MemoryConsolidateTool(bank, ctx.config))
