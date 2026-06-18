"""update_plan built-in tool — faithful port of OpenClaw's update-plan-tool.ts.

Validates a structured model work plan and stores it in the result ``details`` for
UI / transcript consumers (the model's content stays empty, exactly like OpenClaw).
No side effects — a scratchpad the model keeps current while doing non-trivial,
multi-step work. The gating guidance lives in the tool description (OpenClaw keeps
it there, not in a separate prompt section).
"""

from __future__ import annotations

from . import Tool, ToolResult

_PLAN_STEP_STATUSES = ("pending", "in_progress", "completed")


class UpdatePlanTool(Tool):
    name = "update_plan"
    label = "Update Plan"
    default_retryable = False
    # OpenClaw describeUpdatePlanTool() core + explicit decompose-with-tools guidance.
    description = (
        "Update current run plan. Use for non-trivial multi-step work; keep the plan "
        "current while executing. BREAK THE TASK DOWN into the smallest individual "
        "steps, and for EACH step name the specific tool it will use — e.g. "
        "\"web_search: find X\", \"browser: open Y and read Z\", \"computer: click W\", "
        "\"read: open file F\". Short steps; max one `in_progress`; mark steps "
        "completed as you finish them. Skip planning only for simple one-step work."
    )
    parameters = {
        "type": "object",
        "required": ["plan"],
        "properties": {
            "explanation": {
                "type": "string",
                "description": "Short note: what changed.",
            },
            "plan": {
                "type": "array",
                "minItems": 1,
                "description": "Ordered steps; max one in_progress.",
                "items": {
                    "type": "object",
                    "required": ["step", "status"],
                    "additionalProperties": True,
                    "properties": {
                        "step": {"type": "string", "description": "Short step."},
                        "status": {
                            "type": "string",
                            "enum": list(_PLAN_STEP_STATUSES),
                            "description": "pending | in_progress | completed.",
                        },
                    },
                },
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        plan = params.get("plan")
        if not isinstance(plan, list) or not plan:
            return ToolResult.text("plan required", is_error=True)
        # At most one in_progress (else progress state is ambiguous) — OpenClaw rule.
        in_progress = sum(
            1 for s in plan if isinstance(s, dict) and s.get("status") == "in_progress"
        )
        if in_progress > 1:
            return ToolResult.text(
                "plan can contain at most one in_progress step", is_error=True
            )
        details = {"status": "updated", "plan": plan}
        explanation = (params.get("explanation") or "").strip()
        if explanation:
            details["explanation"] = explanation
        # OpenClaw returns EMPTY content + the plan in details (UI/transcript only).
        return ToolResult(content=[], details=details, is_error=False)
