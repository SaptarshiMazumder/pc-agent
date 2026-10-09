"""collection_import — the user's own images and clips (made elsewhere, uploaded) into a collection,
each under the product it shows. Free and instant: the studio calls it after uploading."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.collection_service import CollectionService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class CollectionImportTool(Tool):
    name = "collection_import"
    label = "Add your own files to a collection"
    plugin = PLUGIN
    description = (
        "Add the user's own images and clips (uploads, made outside Ad Studio) to a collection for a post: "
        "`files` [{path, product, note?}] — `product` is the name of the product it shows; `collection` an "
        "existing collection's name, or with `new` the name of one to make."
    )
    parameters = {
        "type": "object",
        "required": ["collection", "files"],
        "properties": {
            "collection": {"type": "string"},
            "new": {"type": "boolean"},
            "files": {"type": "array", "items": {"type": "object"}, "description": "[{path, product, note?}]"},
        },
    }

    def __init__(self, collections: CollectionService) -> None:
        self._collections = collections

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            c = self._collections.import_files(
                str(params.get("collection") or ""), list(params.get("files") or []), bool(params.get("new"))
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"collection_import: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"collection '{c.name}': {len(c.items)} items", details={"collection": c.to_dict()})
