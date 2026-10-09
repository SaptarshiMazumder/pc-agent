"""design_template_save — keep a slide's design as a template, to start new designs from. Free."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.design_template_service import DesignTemplateService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class DesignTemplateSaveTool(Tool):
    name = "design_template_save"
    label = "Save a design as a template"
    plugin = PLUGIN
    description = "Keep a designed slide as a template: `post`, `slide` (its number), `name`, a one-line `description` of what it suits, `tags`."
    parameters = {
        "type": "object",
        "required": ["post", "slide", "name"],
        "properties": {
            "post": {"type": "string"},
            "slide": {"type": "integer"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }

    def __init__(self, templates: DesignTemplateService) -> None:
        self._templates = templates

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            t = self._templates.save_from_slide(
                str(params.get("post") or ""), int(params.get("slide") or 0), str(params.get("name") or ""),
                str(params.get("description") or ""), [str(x) for x in params.get("tags") or []],
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"design_template_save: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"template '{t.slug}' saved", details={"template": t.to_dict()})
