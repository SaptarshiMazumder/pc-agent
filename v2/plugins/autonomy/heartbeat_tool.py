"""heartbeat_respond — the agent records the outcome of one autonomous tick.

Mode-scoped: only exposed on a `heartbeat` run (see domain.agent.apply_mode). The
agent calls it once at the end of a tick to say what happened and whether the user
should be notified. In Phase 2a the outcome rides the normal event stream (visible to
any connected client); durable recording + channel delivery come in 2b/Phase 5.
"""

from __future__ import annotations

from agentd.application.interfaces.tool import Tool, ToolResult


class HeartbeatRespondTool(Tool):
    name = "heartbeat_respond"
    label = "Heartbeat"
    description = (
        "Record the outcome of THIS heartbeat tick. Call once at the end of an "
        "autonomous heartbeat run. If nothing needed attention, set "
        "outcome='nothing-to-do' and notify=false."
    )
    parameters = {
        "type": "object",
        "required": ["outcome"],
        "properties": {
            "outcome": {
                "type": "string",
                "description": "Short outcome — 'nothing-to-do', or what you did.",
            },
            "notify": {
                "type": "boolean",
                "description": "Should the user be notified? Default false.",
            },
            "summary": {
                "type": "string",
                "description": "Human-facing summary to send if notify=true.",
            },
            "next_check": {
                "type": "string",
                "description": "Optional hint for when to check next, e.g. '1h'.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        outcome = (params.get("outcome") or "").strip()
        notify = bool(params.get("notify", False))
        summary = (params.get("summary") or "").strip()
        record = {
            "outcome": outcome,
            "notify": notify,
            "summary": summary,
            "next_check": (params.get("next_check") or "").strip(),
        }
        text = f"heartbeat recorded: {outcome or '(none)'}"
        text += f" — notify user: {summary or outcome}" if notify else " (silent)"
        return ToolResult.text(text, details=record)
