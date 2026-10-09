"""collection_add — images and clips from any campaign into a collection, for a post. Free and instant."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.collection_service import CollectionService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class CollectionAddTool(Tool):
    name = "collection_add"
    label = "Add to a collection"
    plugin = PLUGIN
    description = (
        "Add images and clips from campaigns to a collection (for an Instagram post). `items`: "
        "[{path, campaign, note?}]; `collection`: an existing collection's name, or with `new` the "
        "name of one to make. Usually done by the studio's Generations tab."
    )
    parameters = {
        "type": "object",
        "required": ["collection", "items"],
        "properties": {
            "collection": {"type": "string"},
            "new": {"type": "boolean", "description": "Make a new collection with this name."},
            "items": {"type": "array", "items": {"type": "object"}, "description": "[{path, campaign, note?}]"},
        },
    }

    def __init__(self, collections: CollectionService) -> None:
        self._collections = collections

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            c = self._collections.add(str(params.get("collection") or ""), list(params.get("items") or []), bool(params.get("new")))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"collection_add: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"collection '{c.name}': {len(c.items)} items", details={"collection": c.to_dict()})
