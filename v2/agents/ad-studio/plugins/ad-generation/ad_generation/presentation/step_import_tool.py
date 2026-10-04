"""step_import — the user's own image as a result of an image step: picked, fixed, referenced or
animated like any generated one. Free and instant; the window calls it after uploading a file."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_checklist_editor import CampaignChecklistEditor
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class StepImportTool(Tool):
    name = "step_import"
    label = "Add your image"
    plugin = PLUGIN
    description = (
        "Bring an image the user uploaded (a workspace path) into a campaign's image step as one of "
        "its results — then it can be picked, fixed, used as a reference or as a clip's first frame. "
        "Free, instant, not checked."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "step", "path"],
        "properties": {
            "campaign": {"type": "string"},
            "step": {"type": "string", "description": "An images step, e.g. 'stills'."},
            "path": {"type": "string", "description": "The uploaded image's workspace path (png, jpg, webp)."},
        },
    }

    def __init__(self, editor: CampaignChecklistEditor) -> None:
        self._editor = editor

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            media = self._editor.import_image(
                str(params.get("campaign") or ""), str(params.get("step") or ""), str(params.get("path") or "")
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"step_import: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"{media.step}: added {media.path}", details={"media": media.to_dict()})
