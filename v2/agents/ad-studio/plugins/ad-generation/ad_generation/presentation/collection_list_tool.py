"""collection_list — the collections and their items. Read only."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.collection_service import CollectionService
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class CollectionListTool(Tool):
    name = "collection_list"
    label = "List the collections"
    plugin = PLUGIN
    description = "List the collections and their items (images and clips gathered for posts). Read only."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, collections: CollectionService, workspace: RunWorkspace) -> None:
        self._collections = collections
        self._ws = workspace

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            found = self._collections.all()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"collection_list: {type(e).__name__}: {e}", is_error=True)
        lines = [f"{c.name} ({c.slug}): " + ", ".join(f"{i.kind} of {i.product}" for i in c.items) for c in found]
        return ToolResult.text(
            "\n".join(lines) or "no collections yet",
            details={"root": str(self._ws.root()), "collections": [c.to_dict() for c in found]},
        )
