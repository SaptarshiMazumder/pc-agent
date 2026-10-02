"""cast_list — the cast members and their character sheets, for the window's Cast page. Read only."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_view_service import CampaignViewService
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class CastListTool(Tool):
    name = "cast_list"
    label = "List the cast"
    plugin = PLUGIN
    description = "List the cast members: name, description and character sheet. Read only."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, views: CampaignViewService, workspace: RunWorkspace) -> None:
        self._views = views
        self._ws = workspace

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            members = self._views.cast()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"cast_list: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            "\n".join(m["name"] for m in members) or "no cast yet",
            details={"root": str(self._ws.root()), "cast": members},
        )
