"""commitment — track open loops / follow-ups the agent owes (S15).

The agent records a promise/follow-up ("circle back to Jane Friday"), lists what's open,
and marks them done/dropped. Context-aware: scoped to the calling agent via run-context.
A structured complement to memory (durable facts) and cron (timed execution).
"""

from __future__ import annotations

import time

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_run_context
from agentd.domain.commitment import Commitment


def _agent_id() -> str:
    ctx = current_run_context()
    return (ctx.agent_id if ctx else None) or "main"


def _fmt(c) -> str:
    when = f" (due {time.strftime('%Y-%m-%d %H:%M', time.localtime(c.due_at))})" if c.due_at else ""
    return f"[{c.id}] {c.text}{when}"


class CommitmentTool(Tool):
    name = "commitment"
    label = "Commitment"
    description = (
        "Track open loops / follow-ups you owe. action='add' (text, optional due ISO) records "
        "a promise; action='list' shows what's still open; action='done'/'drop' (id) resolves "
        "one. Use it so you don't forget to circle back."
    )
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "done", "drop"]},
            "text": {"type": "string", "description": "the follow-up (for add)"},
            "due": {
                "type": "string",
                "description": "optional due time, ISO e.g. 2026-06-25T17:00",
            },
            "id": {"type": "string", "description": "commitment id (for done/drop)"},
        },
    }

    def __init__(self, store):
        self._store = store

    async def execute(self, tool_call_id, params, abort, on_update=None):
        action = (params.get("action") or "").strip().lower()
        if action == "list":
            items = self._store.commitments(_agent_id(), open_only=True)
            return ToolResult.text(
                "Open commitments:\n" + "\n".join(_fmt(c) for c in items)
                if items
                else "(no open commitments)",
                details=[c.__dict__ for c in items],
            )
        if action == "add":
            text = (params.get("text") or "").strip()
            if not text:
                return ToolResult.text("a commitment needs 'text'", is_error=True)
            due_at = None
            raw = (params.get("due") or "").strip()
            if raw:
                from datetime import datetime

                try:
                    dt = datetime.fromisoformat(raw)
                    due_at = (dt if dt.tzinfo else dt.astimezone()).timestamp()
                except ValueError:
                    return ToolResult.text(
                        f"invalid 'due' (ISO like 2026-06-25T17:00): {raw}", is_error=True
                    )
            cid = self._store.add_commitment(
                Commitment(
                    id="", agent_id=_agent_id(), text=text, due_at=due_at, created_at=time.time()
                )
            )
            return ToolResult.text(f"tracked [{cid}]: {text}", details={"id": cid})
        if action in ("done", "drop"):
            cid = (params.get("id") or "").strip()
            ok = self._store.resolve_commitment(cid, "done" if action == "done" else "dropped")
            return ToolResult.text(
                f"{action} {cid}" if ok else f"no such commitment: {cid}", is_error=not ok
            )
        return ToolResult.text("action must be add / list / done / drop", is_error=True)
