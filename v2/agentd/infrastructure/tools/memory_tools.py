"""Memory tools — recall + write the agent's long-term memory (Phase 3 / S5).

`remember` writes a durable fact; `memory_search` recalls by keyword across past sessions;
`memory_get` fetches one item. Context-aware: scoped to the calling agent via the run
context (same pattern as cron/goal). Built only when memory is enabled + a bank is wired.
"""

from __future__ import annotations

import time

from agentd.application.run_context import current_run_context
from agentd.domain.memory import MemoryItem

from . import Tool, ToolResult


def _agent_id() -> str:
    ctx = current_run_context()
    return (ctx.agent_id if ctx else None) or "main"


class RememberTool(Tool):
    name = "remember"
    label = "Remember"
    description = (
        "Save a durable fact/learning to your long-term memory so you (or a future session) "
        "can recall it later — a user preference, where a credential lives, a gotcha, a "
        "decision. Keep each note to one clear sentence.")
    parameters = {
        "type": "object", "required": ["text"],
        "properties": {"text": {"type": "string", "description": "The fact to remember (one sentence)."}},
    }

    def __init__(self, bank):
        self._bank = bank

    async def execute(self, tool_call_id, params, abort, on_update=None):
        text = (params.get("text") or "").strip()
        if not text:
            return ToolResult.text("nothing to remember (empty text)", is_error=True)
        mid = self._bank.save(MemoryItem(
            id="", agent_id=_agent_id(), source="note", text=text, created_at=time.time()))
        return ToolResult.text(f"remembered [{mid}]: {text}", details={"id": mid})


class MemorySearchTool(Tool):
    name = "memory_search"
    label = "Memory"
    description = (
        "Search your long-term memory for facts you saved earlier (across past sessions). "
        "Check here before asking the user something you might already know.")
    parameters = {
        "type": "object", "required": ["query"],
        "properties": {"query": {"type": "string"},
                       "limit": {"type": "integer", "description": "max results (default 5)"}},
    }

    def __init__(self, bank):
        self._bank = bank

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            limit = max(1, min(int(params.get("limit", 5)), 20))
        except (TypeError, ValueError):
            limit = 5
        hits = self._bank.search(_agent_id(), (params.get("query") or "").strip(), limit=limit)
        if not hits:
            return ToolResult.text("(no matching memories)")
        return ToolResult.text(
            "Memories:\n" + "\n".join(f"[{h.id}] {h.text}" for h in hits),
            details=[h.__dict__ for h in hits])


class MemoryGetTool(Tool):
    name = "memory_get"
    label = "Memory"
    description = "Fetch one memory item by its id (from memory_search)."
    parameters = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}

    def __init__(self, bank):
        self._bank = bank

    async def execute(self, tool_call_id, params, abort, on_update=None):
        item = self._bank.get((params.get("id") or "").strip())
        if not item:
            return ToolResult.text("(no such memory)", is_error=True)
        return ToolResult.text(item.text, details=item.__dict__)


class MemoryConsolidateTool(Tool):
    name = "memory_consolidate"
    label = "Memory"
    description = (
        "Tidy your long-term memory: collapse exact-duplicate notes (keeps the newest). "
        "Good to run on a schedule so repeatedly-saved facts don't pile up.")
    parameters = {"type": "object", "properties": {}}

    def __init__(self, bank):
        self._bank = bank

    async def execute(self, tool_call_id, params, abort, on_update=None):
        from agentd.infrastructure.memory.consolidate import consolidate

        removed = consolidate(self._bank, _agent_id())
        return ToolResult.text(f"consolidated memory: removed {removed} duplicate note(s)",
                               details={"removed": removed})
