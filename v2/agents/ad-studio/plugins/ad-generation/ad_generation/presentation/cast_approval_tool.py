"""cast_approval — the studio's click: approve making a proposed cast member on a model, or dismiss
the proposal. WINDOW ONLY: an approval recorded from a model turn would be the agent approving
itself."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.one_time_approvals import OneTimeApprovals
from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.run_gate import from_window


class CastApprovalTool(Tool):
    name = "cast_approval"
    label = "Approve a cast member"
    plugin = PLUGIN
    description = "The studio's own control for approving or dismissing a proposed cast member. Never call it yourself."
    parameters = {
        "type": "object",
        "required": ["name", "action"],
        "properties": {
            "name": {"type": "string"},
            "action": {"type": "string", "enum": ["approve", "dismiss"]},
            "model": {"type": "string", "description": "approve: provider/model."},
        },
    }

    def __init__(self, approvals: OneTimeApprovals) -> None:
        self._approvals = approvals

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            name = str(params.get("name") or "").strip()
            if params.get("action") == "dismiss":
                self._approvals.dismiss(name)
                return ToolResult.text(f"cast member {name}: proposal dismissed", details={"dismissed": name})
            if not from_window():
                raise PermissionError("only the user's click in the studio approves a cast member")
            token = self._approvals.approve(name, str(params.get("model") or "").strip())
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"cast_approval: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"cast member {name}: approved", details={"token": token})
