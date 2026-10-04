"""campaign_settings — the studio's switch for whether each run needs the user's approval ("ask")
or not ("auto"). WINDOW ONLY: the agent cannot switch approval off for itself."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.run_approvals import RunApprovals
from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.run_gate import from_window


class CampaignSettingsTool(Tool):
    name = "campaign_settings"
    label = "Campaign settings"
    plugin = PLUGIN
    description = "The studio's own switch for run approval (ask / auto). Never call it yourself."
    parameters = {
        "type": "object",
        "required": ["campaign", "approval"],
        "properties": {"campaign": {"type": "string"}, "approval": {"type": "string", "enum": ["ask", "auto"]}},
    }

    def __init__(self, approvals: RunApprovals) -> None:
        self._approvals = approvals

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            if not from_window():
                raise PermissionError("only the user changes this, in the studio")
            self._approvals.set_mode(str(params.get("campaign") or ""), str(params.get("approval") or ""))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"campaign_settings: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"approval: {params.get('approval')}", details={"approval": params.get("approval")})
