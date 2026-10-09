"""design_reference_list — the design references kept: each one's picture, what it suits and its
layout. Read only."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.design_reference_service import DesignReferenceService
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class DesignReferenceListTool(Tool):
    name = "design_reference_list"
    label = "List design references"
    plugin = PLUGIN
    description = "List the design references in the library — each with its picture, what it suits and its layout. Read only."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, references: DesignReferenceService, workspace: RunWorkspace) -> None:
        self._references = references
        self._ws = workspace

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            found = self._references.all()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"design_reference_list: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            "\n".join(f"{r.slug} ({', '.join(r.suits) or '—'}): {r.spec.get('layout', '')}" for r in found) or "no references yet",
            details={"root": str(self._ws.root()), "references": [r.to_dict() for r in found]},
        )
