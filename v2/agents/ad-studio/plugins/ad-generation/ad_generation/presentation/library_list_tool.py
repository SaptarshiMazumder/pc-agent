"""library_list — everything in the account's library: every campaign's generations and the user's
own uploads, newest first. Read only."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.library_service import LibraryService
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class LibraryListTool(Tool):
    name = "library_list"
    label = "List the library"
    plugin = PLUGIN
    description = "List the library: every campaign's generations and the user's own uploads, newest first. Read only."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, library: LibraryService, workspace: RunWorkspace) -> None:
        self._library = library
        self._ws = workspace

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            items, campaigns = self._library.all(), self._library.campaigns()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"library_list: {type(e).__name__}: {e}", is_error=True)
        uploads = sum(1 for i in items if i.source == "upload")
        return ToolResult.text(
            f"{len(items)} in the library: {len(items) - uploads} generation(s), {uploads} upload(s)",
            details={"root": str(self._ws.root()), "items": [i.to_dict() for i in items], "campaigns": campaigns},
        )
