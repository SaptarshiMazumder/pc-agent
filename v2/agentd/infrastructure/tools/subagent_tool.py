"""spawn_subagent — delegate a self-contained subtask to a fresh sub-agent (S8/S9).

The agent hands off heavy or parallelizable work (a long read, research, a separate
analysis) to a child run and gets its result back, instead of doing everything in one
thread. The actual child run lives in the gateway; it's injected here as a callable so this
infrastructure tool never imports the presentation layer.
"""

from __future__ import annotations

from . import Tool, ToolResult


class SpawnSubagentTool(Tool):
    name = "spawn_subagent"
    label = "Subagent"
    concurrency = "parallel"          # the agent may fan several out in one turn
    default_timeout_sec = None        # a child run can take a while — don't let the guard time it out
    default_retryable = False         # expensive + side-effecting: never auto-retry
    description = (
        "Delegate a self-contained subtask to a fresh sub-agent and get its result back. "
        "Use it to parallelize or offload heavy work (research, a long read, a separate "
        "analysis) rather than doing everything yourself. Give a COMPLETE, standalone task — "
        "the sub-agent does NOT see this conversation. You may call it several times in one "
        "turn to run tasks in parallel.")
    parameters = {
        "type": "object", "required": ["task"],
        "properties": {
            "task": {"type": "string", "description": "the complete, standalone task for the sub-agent"},
            "agent": {"type": "string", "description": "optional: run it as this agent id (default: yours)"},
        },
    }

    def __init__(self, spawn_fn):
        self._spawn = spawn_fn        # async (agent_id|None, task) -> str

    async def execute(self, tool_call_id, params, abort, on_update=None):
        task = (params.get("task") or "").strip()
        if not task:
            return ToolResult.text("spawn_subagent needs a 'task'", is_error=True)
        agent = (params.get("agent") or "").strip() or None
        try:
            result = await self._spawn(agent, task)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"subagent failed: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(result or "(subagent returned no result)")
