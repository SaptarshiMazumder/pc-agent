"""library_to_collection — any mix of library items (generations and uploads) into a collection, an
existing one or a new one. Free and instant."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.library_service import LibraryService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class LibraryToCollectionTool(Tool):
    name = "library_to_collection"
    label = "Add library items to a collection"
    plugin = PLUGIN
    description = (
        "Put library items into a collection for a post: `paths` (from library_list), `collection` an existing "
        "collection's name, or with `new` the name of one to make."
    )
    parameters = {
        "type": "object",
        "required": ["collection", "paths"],
        "properties": {
            "collection": {"type": "string"},
            "new": {"type": "boolean"},
            "paths": {"type": "array", "items": {"type": "string"}},
        },
    }

    def __init__(self, library: LibraryService) -> None:
        self._library = library

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            c = self._library.to_collection(
                str(params.get("collection") or ""), bool(params.get("new")), [str(p) for p in params.get("paths") or []]
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"library_to_collection: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"collection '{c.name}': {len(c.items)} items", details={"collection": c.to_dict()})
