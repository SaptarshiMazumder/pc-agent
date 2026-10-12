"""library_remove — take one of the user's own uploads out of the library. Generations stay with
their campaigns. Free."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.library_service import LibraryService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class LibraryRemoveTool(Tool):
    name = "library_remove"
    label = "Remove an upload from the library"
    plugin = PLUGIN
    description = "Remove one of the user's own uploads from the library: `path`. Generations cannot be removed here."
    parameters = {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}

    def __init__(self, library: LibraryService) -> None:
        self._library = library

    async def execute(self, tool_call_id, params, abort, on_update=None):
        path = str(params.get("path") or "")
        try:
            self._library.remove(path)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"library_remove: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(f"{path} removed", details={"removed": path})
