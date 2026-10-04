"""step_add — a step of the user's own, for what the recipe does not have ("a product-only tabletop
image", "a second scene at night"). It runs like any other step (step_run); nothing else changes."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_checklist_editor import CampaignChecklistEditor
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class StepAddTool(Tool):
    name = "step_add"
    label = "Add a step"
    plugin = PLUGIN
    description = (
        "Add a step to a campaign's checklist for something its recipe does not have. Give it a "
        "title and an action (images | video | product_sheet — six views of the product, used as references "
        "by every later image and clip); for images, write the full prompt from the user's "
        "words (who and what appears, the place, the light) and say whether the cast member and the "
        "product appear; for video, name the image step it starts from (source). It never rewrites "
        "the brief or another step. Run it with step_run when the user asks."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "title", "action"],
        "properties": {
            "campaign": {"type": "string"},
            "title": {"type": "string", "description": "Short, e.g. 'Tabletop still'."},
            "action": {"type": "string", "enum": ["images", "video", "product_sheet"]},
            "prompt": {"type": "string", "description": "The full prompt (or leave it out and name a brief `scene`)."},
            "scene": {"type": "string", "description": "A scene of the brief whose prompt it uses, e.g. 's1'."},
            "references": {"type": "array", "items": {"type": "string"}, "description": "Reference images (workspace paths); left out = the cast and product defaults."},
            "cast": {"type": "boolean", "description": "The cast member appears (default true)."},
            "shows_product": {"type": "boolean", "description": "The product appears (default true)."},
            "source": {"type": "string", "description": "video: the image step it starts from."},
            "count": {"type": "integer", "description": "images per run (default the recipe's)."},
            "after": {"type": "string", "description": "The step it goes after; default the end."},
        },
    }

    def __init__(self, editor: CampaignChecklistEditor) -> None:
        self._editor = editor

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            step = self._editor.add(
                str(params.get("campaign") or ""),
                CampaignStep(
                    id="",
                    title=str(params.get("title") or "").strip() or "Step",
                    action=str(params.get("action") or ""),
                    scene=str(params.get("scene") or ""),
                    source=str(params.get("source") or ""),
                    prompt=str(params.get("prompt") or "").strip(),
                    references=tuple(str(r) for r in params.get("references") or ()),
                    cast=bool(params.get("cast", True)),
                    shows_product=bool(params.get("shows_product", True)),
                    count=int(params.get("count") or 0),
                ),
                str(params.get("after") or ""),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"step_add: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            f"added step '{step.id}' ({step.title}, {step.action}). Run it with step_run when the user asks.",
            details={"step": step.to_dict()},
        )
