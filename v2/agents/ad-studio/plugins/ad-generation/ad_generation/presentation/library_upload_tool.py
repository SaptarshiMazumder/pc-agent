"""library_upload — the user's own images and clips (uploaded by the window) kept in the library,
each with an optional label (the product it shows). Free and instant."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.library_service import LibraryService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class LibraryUploadTool(Tool):
    name = "library_upload"
    label = "Add files to the library"
    plugin = PLUGIN
    description = "Keep the user's own images and clips in the library: `files` [{path (an upload), label? — the product it shows}]."
    parameters = {
        "type": "object",
        "required": ["files"],
        "properties": {"files": {"type": "array", "items": {"type": "object"}, "description": "[{path, label?}]"}},
    }

    def __init__(self, library: LibraryService) -> None:
        self._library = library

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            added = self._library.upload(list(params.get("files") or []))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"library_upload: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"{len(added)} added to the library", details={"items": [i.to_dict() for i in added]})
