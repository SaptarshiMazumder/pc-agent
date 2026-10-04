"""step_pick — the result a step carries forward (a video step starts from its source step's pick).
Free and instant; the window calls it directly when the user clicks a result."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_checklist_editor import CampaignChecklistEditor
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class StepPickTool(Tool):
    name = "step_pick"
    label = "Pick a result"
    plugin = PLUGIN
    description = "Pick one of a step's results as the one it carries forward. Free, instant."
    parameters = {
        "type": "object",
        "required": ["campaign", "step", "path"],
        "properties": {
            "campaign": {"type": "string"},
            "step": {"type": "string"},
            "path": {"type": "string", "description": "Workspace path of one of the step's results."},
        },
    }

    def __init__(self, editor: CampaignChecklistEditor) -> None:
        self._editor = editor

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            step = self._editor.pick(str(params.get("campaign") or ""), str(params.get("step") or ""), str(params.get("path") or ""))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"step_pick: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"{step.id}: picked {step.pick}", details={"step": step.to_dict()})
