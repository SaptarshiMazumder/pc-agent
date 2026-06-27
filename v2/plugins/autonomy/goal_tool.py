"""goal — track a long task's objective + optional token budget (Phase 2b).

A leash for long/autonomous runs: the agent sets an objective, checks it to stay
on-target, and marks it complete/blocked. The budget is advisory in this phase (the
agent self-limits via the prompt); hard loop-enforcement is a later refinement. The
goal is scoped to the calling session (from the run-context).
"""

from __future__ import annotations

import time
import uuid

from agentd.application.run_context import current_run_context
from agentd.domain.autonomy import Goal

from agentd.application.interfaces.tool import Tool, ToolResult


class GoalTool(Tool):
    name = "goal"
    label = "Goal"
    description = (
        "Track a long task's objective so you stay on-target across many turns/ticks. "
        "action='create' (objective, optional token_budget) starts one; action='get' "
        "recalls the current objective + budget; action='update' (status: complete|"
        "blocked) closes it. On a long autonomous run: set a goal, check 'get' "
        "periodically, and stop when the objective is met or the budget is nearly spent."
    )
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["create", "get", "update"]},
            "objective": {"type": "string", "description": "What to achieve (action=create)."},
            "token_budget": {"type": "integer",
                             "description": "Optional advisory token budget (action=create)."},
            "status": {"type": "string", "enum": ["complete", "blocked"],
                       "description": "How the goal ended (action=update)."},
        },
    }
    default_retryable = False
    concurrency = "sequential"

    def __init__(self, goal_store):
        self._store = goal_store

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ctx = current_run_context()
        agent_id = ctx.agent_id if ctx else "main"
        session_key = ctx.session_key if ctx else "default"
        action = (params.get("action") or "").strip()

        if action == "create":
            objective = (params.get("objective") or "").strip()
            if not objective:
                return ToolResult.text("objective is required for action=create", is_error=True)
            goal = Goal(id=uuid.uuid4().hex[:12], agent_id=agent_id, session_key=session_key,
                        objective=objective, token_budget=params.get("token_budget"),
                        status="active", created_at=time.time())
            self._store.create_goal(goal)
            budget = f" (budget {goal.token_budget} tokens)" if goal.token_budget else ""
            return ToolResult.text(f"goal set: {objective}{budget}", details=goal.__dict__)

        if action == "get":
            goal = self._store.active_goal(session_key)
            if goal is None:
                return ToolResult.text("No active goal.")
            budget = f" | budget: {goal.token_budget} tokens" if goal.token_budget else ""
            return ToolResult.text(
                f"objective: {goal.objective}{budget} | status: {goal.status}",
                details=goal.__dict__)

        if action == "update":
            status = (params.get("status") or "").strip()
            goal = self._store.active_goal(session_key)
            if goal is None:
                return ToolResult.text("No active goal to update.", is_error=True)
            if status not in ("complete", "blocked"):
                return ToolResult.text("status must be 'complete' or 'blocked'", is_error=True)
            self._store.update_goal(goal.id, status)
            return ToolResult.text(f"goal {status}: {goal.objective}")

        return ToolResult.text(f"unknown action: {action}", is_error=True)
