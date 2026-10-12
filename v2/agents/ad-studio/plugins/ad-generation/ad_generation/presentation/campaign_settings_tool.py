"""campaign_settings — the studio's switch for whether each run needs the user's approval ("ask")
or not ("auto"). WINDOW ONLY: the agent cannot switch approval off for itself."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_checklist_editor import CampaignChecklistEditor
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
        "required": ["campaign"],
        "properties": {
            "campaign": {"type": "string"},
            "approval": {"type": "string", "enum": ["ask", "auto"]},
            "auto_pick": {"type": "boolean", "description": "The Auto switch: true lets the agent pick unchosen settings."},
            "step": {"type": "string", "description": "With auto_pick: that step's own switch instead of the campaign's."},
            "inherit": {"type": "boolean", "description": "With step: follow the campaign's switch again."},
        },
    }

    def __init__(self, approvals: RunApprovals, editor: CampaignChecklistEditor) -> None:
        self._approvals = approvals
        self._editor = editor

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            if not from_window():
                raise PermissionError("only the user changes this, in the studio")
            campaign = str(params.get("campaign") or "")
            if params.get("approval"):
                self._approvals.set_mode(campaign, str(params["approval"]))
            if "auto_pick" in params or params.get("inherit"):
                self._editor.set_auto(campaign, bool(params.get("auto_pick")), str(params.get("step") or ""), bool(params.get("inherit")))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"campaign_settings: {type(e).__name__}: {e}", is_error=True)
        changed = {k: params[k] for k in ("approval", "auto_pick", "step", "inherit") if k in params}
        return ToolResult.text(f"settings: {changed}", details=changed)
