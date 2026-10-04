"""run_approval — the studio's click: approve one run before sending it, or dismiss a proposal.
WINDOW ONLY: an approval recorded from a model turn would be the agent approving itself."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.run_approvals import RunApprovals
from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.run_gate import from_window


class RunApprovalTool(Tool):
    name = "run_approval"
    label = "Approve a run"
    plugin = PLUGIN
    description = "The studio's own control for approving a run or dismissing a proposal. Never call it yourself."
    parameters = {
        "type": "object",
        "required": ["campaign", "step", "action"],
        "properties": {
            "campaign": {"type": "string"},
            "step": {"type": "string"},
            "action": {"type": "string", "enum": ["approve", "dismiss"]},
            "tool": {"type": "string", "enum": ["step_run", "still_fix", "clip_edit"]},
            "args": {"type": "object", "description": "The run's model, count, length, resolution and target."},
        },
    }

    def __init__(self, approvals: RunApprovals) -> None:
        self._approvals = approvals

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            campaign, step = str(params.get("campaign") or ""), str(params.get("step") or "")
            if params.get("action") == "dismiss":
                self._approvals.dismiss(campaign, step)
                return ToolResult.text(f"{step}: proposal dismissed", details={"dismissed": step})
            if not from_window():
                raise PermissionError("only the user's click in the studio approves a run")
            token = self._approvals.approve(campaign, step, str(params.get("tool") or ""), dict(params.get("args") or {}))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"run_approval: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"{step}: approved", details={"token": token})
