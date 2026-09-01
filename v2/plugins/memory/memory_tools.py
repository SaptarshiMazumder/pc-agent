"""Memory tools — recall + write the agent's long-term memory (Phase 3 / S5).

`remember` writes a durable fact; `memory_search` recalls by keyword across past sessions;
`memory_get` fetches one item. Context-aware: scoped to the calling agent via the run
context (same pattern as cron/goal). Built only when memory is enabled + a bank is wired.
"""

from __future__ import annotations

import time

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_run_context
from agent_runtime.domain.memory import MemoryItem
from agent_runtime.infrastructure import accounts


def _agent_id() -> str:
    """The memory partition for this run: the calling agent, namespaced by the CURRENT account
    (hosted) so users' notes stay separate. Bare agent id on desktop/local. One place, so every
    memory tool (remember/search/consolidate) is isolated together."""
    ctx = current_run_context()
    base = (ctx.agent_id if ctx else None) or "main"
    return accounts.memory_partition(base)


class RememberTool(Tool):
    name = "remember"
    label = "Remember"
    description = (
        "Save a durable fact/learning to your long-term memory so you (or a future session) "
        "can recall it later — a user preference, where a credential lives, a gotcha, a "
        "decision. Keep each note to one clear sentence."
    )
    parameters = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "description": "The fact to remember (one sentence)."}
        },
    }

    def __init__(self, bank, embedder=None):
        self._bank = bank
        self._embedder = embedder  # BackgroundEmbedder: fills the vector off the turn

    async def execute(self, tool_call_id, params, abort, on_update=None):
        text = (params.get("text") or "").strip()
        if not text:
            return ToolResult.text("nothing to remember (empty text)", is_error=True)
        item = MemoryItem(
            id="", agent_id=_agent_id(), source="note", text=text, created_at=time.time()
        )
        # Fast path: write the fact NOW (no network), embed in the background so the agent doesn't
        # wait. Falls back to a synchronous save when there's no embedder (keyword-only mode).
        if self._embedder is not None and getattr(self._bank, "embedder_ready", False):
            mid = self._bank.save_pending(item)
            self._embedder.schedule(mid, text)
        else:
            mid = self._bank.save(item)
        return ToolResult.text(f"remembered [{mid}]: {text}", details={"id": mid})


class MemorySearchTool(Tool):
    name = "memory_search"
    label = "Memory"
    description = (
        "Search your long-term memory for facts you saved earlier (across past sessions). "
        "Check here before asking the user something you might already know."
    )
    parameters = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "description": "max results (default 5)"},
        },
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
            details=[h.__dict__ for h in hits],
        )


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
        "Consolidate your long-term memory ('dreaming'): collapse duplicate/near-duplicate "
        "notes, promote facts you keep recalling into durable long-term memory, and forget "
        "stale never-recalled ones. Run it on a schedule (e.g. a nightly cron) so memory stays "
        "clean and gets sharper over time."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, bank, config=None):
        self._bank = bank
        self._config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        from agent_runtime.infrastructure.memory.consolidate import consolidate
        from agent_runtime.infrastructure.memory.dreaming import dream

        agent_id = _agent_id()
        removed = consolidate(self._bank, agent_id)  # exact dups (works without embeddings)
        d = dream(self._bank, agent_id, self._config or _DefaultDreamCfg())
        d["removed_exact"] = removed
        return ToolResult.text(
            f"dreamed: merged {d['merged']} near-dup(s), promoted {d['promoted']} to long-term, "
            f"forgot {d['forgotten']} stale, removed {removed} exact dup(s)",
            details=d,
        )


class _DefaultDreamCfg:
    """Fallback thresholds when the tool is built without config (dream() reads via getattr)."""
