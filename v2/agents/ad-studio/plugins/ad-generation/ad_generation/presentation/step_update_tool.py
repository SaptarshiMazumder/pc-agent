"""step_update — change a step in place: rename it, give it its own prompt or references, point a
video step at another image step, skip it or open it again. Its results stay."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_checklist_editor import CampaignChecklistEditor
from ad_generation.presentation.generation_backend_resolver import PLUGIN

_FIELDS = ("title", "prompt", "references", "scene", "source", "status", "cast", "shows_product", "count", "text")


class StepUpdateTool(Tool):
    name = "step_update"
    label = "Change a step"
    plugin = PLUGIN
    description = (
        "Change one step of a campaign's checklist when the user deviates: its title, its own "
        "prompt or references, the brief scene it uses, the image step a video starts from "
        "(source), how many images it makes, or its status ('skipped' to skip it, 'todo' to open "
        "it again). Only what is passed changes; its results and pick stay."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "step"],
        "properties": {
            "campaign": {"type": "string"},
            "step": {"type": "string"},
            "title": {"type": "string"},
            "prompt": {"type": "string"},
            "references": {"type": "array", "items": {"type": "string"}},
            "scene": {"type": "string"},
            "source": {"type": "string"},
            "status": {"type": "string", "enum": ["todo", "done", "skipped"]},
            "text": {"type": "string", "enum": ["", "overlay"], "description": "A text ad's designs: 'overlay' sets the words in real fonts as editable layers; '' lets the image model draw them."},
            "cast": {"type": "boolean"},
            "shows_product": {"type": "boolean"},
            "count": {"type": "integer"},
            "settings": {"type": "object", "description": "The user's choices for this step's settings, by key (cast, aspect_ratio, location, count, image_model, video_model, seconds, resolution…) — only what the user chose; \"\" clears one."},
        },
    }

    def __init__(self, editor: CampaignChecklistEditor) -> None:
        self._editor = editor

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            changes = {k: params[k] for k in _FIELDS if k in params}
            campaign, step_id = str(params.get("campaign") or ""), str(params.get("step") or "")
            if not changes and not params.get("settings"):
                raise ValueError("say what to change")
            step = self._editor.choose(campaign, step_id, dict(params["settings"])) if params.get("settings") else None
            if changes:
                step = self._editor.update(campaign, step_id, changes)
            changes = {**changes, **({"settings": params["settings"]} if params.get("settings") else {})}
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"step_update: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"step '{step.id}' changed: {', '.join(changes)}", details={"step": step.to_dict()})
