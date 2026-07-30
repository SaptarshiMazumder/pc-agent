"""message_agent — send a task to ANOTHER persistent agent and get its reply (A5).

Unlike ``spawn_subagent`` (a fresh ephemeral child that borrows a definition and then vanishes),
this reaches the target agent's OWN ongoing session, so the specialist remembers prior exchanges
with the caller. The actual run lives in the gateway; it's injected here as a callable so this
infrastructure tool never imports the presentation layer. ``concurrency="parallel"`` so several
agents can be messaged in one turn (they run concurrently)."""

from __future__ import annotations

from . import Tool, ToolResult


class MessageAgentTool(Tool):
    name = "message_agent"
    label = "Message Agent"
    concurrency = "parallel"  # several agents can be messaged at once
    default_timeout_sec = None  # the other agent's turn can take a while
    default_retryable = False  # side-effecting (runs another agent); never auto-retry
    description = (
        "Send a message or task to ANOTHER persistent agent and get its reply back. Unlike "
        "spawn_subagent (a throwaway helper with no memory), this reaches the agent's OWN "
        "ongoing session, so it remembers your past exchanges. Use `agents_list` to see who you "
        "can reach; you may message several agents in one turn (they run in parallel)."
    )
    parameters = {
        "type": "object",
        "required": ["agent", "message"],
        "properties": {
            "agent": {"type": "string", "description": "the target agent's id"},
            "message": {"type": "string", "description": "the message / task for that agent"},
        },
    }

    def __init__(self, message_fn):
        self._message = message_fn  # async (target_id, message) -> str

    async def execute(self, tool_call_id, params, abort, on_update=None):
        target = (params.get("agent") or "").strip()
        message = (params.get("message") or "").strip()
        if not target or not message:
            return ToolResult.text("message_agent needs 'agent' and 'message'", is_error=True)
        try:
            reply = await self._message(target, message)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"message_agent failed: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(reply or "(no reply)")
