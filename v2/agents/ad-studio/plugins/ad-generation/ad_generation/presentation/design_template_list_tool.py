"""design_template_list — the design templates (built-in and the user's own), each with what it is
for and its preview. Read only."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.design_template_service import DesignTemplateService
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class DesignTemplateListTool(Tool):
    name = "design_template_list"
    label = "List design templates"
    plugin = PLUGIN
    description = "List the slide design templates — sale poster, editorial card, collage, video lower third… — each with what it suits and its preview. Read only."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, templates: DesignTemplateService, workspace: RunWorkspace) -> None:
        self._templates = templates
        self._ws = workspace

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            found = self._templates.all()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"design_template_list: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            "\n".join(f"{t.slug} ({t.kind}{', yours' if t.origin == 'saved' else ''}): {t.description}" for t in found),
            details={"root": str(self._ws.root()), "templates": [t.to_dict() for t in found]},
        )
