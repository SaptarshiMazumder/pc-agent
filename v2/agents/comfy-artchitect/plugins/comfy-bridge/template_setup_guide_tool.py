"""template_setup_guide — the WINDOW's call when a chat is saved as a template.

It hands back the setup guide the chat's installer lists add up to, and the gaps: every model
and node pack no list could say where to get. The Save dialog shows each gap as a field for its
link and keeps the template only when none is left empty — the window writes `setup.json`
(the Library's one writer is the window). The model never calls this.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from template_setup_guide_builder import TemplateSetupGuideBuilder


class TemplateSetupGuideTool(Tool):
    name = "template_setup_guide"
    label = "Template setup guide"
    default_retryable = True
    description = (
        "For the WINDOW's Save-as-template dialog: the setup guide a chat's installer lists add up "
        "to, and what they could not source. The model has no reason to call it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "manifests": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Workspace-relative paths of the chat's install_<role>.manifest.json files.",
            },
        },
        "required": ["manifests"],
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        paths = [str(p) for p in params.get("manifests") or []]
        try:
            guide, gaps = TemplateSetupGuideBuilder(Path(current_workspace(".") or ".")).build(paths)
        except (OSError, ValueError) as e:
            return ToolResult.text(f"template_setup_guide: {e}", is_error=True)
        return ToolResult.text(
            f"{len(guide.node_packs)} node pack(s), {len(guide.models)} model(s), {len(gaps)} gap(s)",
            details={"guide": guide.to_dict(), "gaps": [asdict(g) for g in gaps]},
        )
