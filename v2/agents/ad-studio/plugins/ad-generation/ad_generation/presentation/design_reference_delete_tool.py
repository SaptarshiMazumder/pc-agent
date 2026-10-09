"""design_reference_delete — drop a design reference from the library. Free."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.design_reference_service import DesignReferenceService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class DesignReferenceDeleteTool(Tool):
    name = "design_reference_delete"
    label = "Delete a design reference"
    plugin = PLUGIN
    description = "Drop a design reference from the library: `reference` (its slug)."
    parameters = {"type": "object", "required": ["reference"], "properties": {"reference": {"type": "string"}}}

    def __init__(self, references: DesignReferenceService) -> None:
        self._references = references

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            self._references.delete(str(params.get("reference") or ""))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"design_reference_delete: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"reference '{params.get('reference')}' deleted", details={"deleted": str(params.get("reference"))})
