"""collection_update — remove items, reorder them, rename or delete a collection. Free and instant."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.collection_service import CollectionService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class CollectionUpdateTool(Tool):
    name = "collection_update"
    label = "Change a collection"
    plugin = PLUGIN
    description = (
        "Remove items from a collection, put them in a new order, rename it, delete it, or record where a "
        "product was found (`product`: {name, found_at, link, price} — only what the user gave)."
    )
    parameters = {
        "type": "object",
        "required": ["collection", "action"],
        "properties": {
            "collection": {"type": "string", "description": "Its slug."},
            "action": {"type": "string", "enum": ["remove", "reorder", "rename", "delete", "product"]},
            "product": {"type": "object", "description": "product: {name, found_at, link, price}, exactly as the user gave them."},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "remove: these; reorder: every item, in the new order."},
            "name": {"type": "string", "description": "rename: the new name."},
        },
    }

    def __init__(self, collections: CollectionService) -> None:
        self._collections = collections

    async def execute(self, tool_call_id, params, abort, on_update=None):
        slug, action = str(params.get("collection") or ""), str(params.get("action") or "")
        paths = [str(p) for p in params.get("paths") or []]
        try:
            if action == "delete":
                self._collections.delete(slug)
                return ToolResult.text(f"collection {slug} deleted", details={"deleted": slug})
            if action == "remove":
                c = self._collections.remove(slug, paths)
            elif action == "reorder":
                c = self._collections.reorder(slug, paths)
            elif action == "product":
                c = self._collections.set_product(slug, dict(params.get("product") or {}))
            elif action == "rename":
                c = self._collections.rename(slug, str(params.get("name") or ""))
            else:
                raise ValueError(f"action is remove / reorder / rename / delete / product, not '{action}'")
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"collection_update: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"collection '{c.name}': {len(c.items)} items", details={"collection": c.to_dict()})
