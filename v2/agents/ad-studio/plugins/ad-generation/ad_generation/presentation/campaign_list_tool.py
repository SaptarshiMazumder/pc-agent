"""campaign_list — every campaign, for the window's Campaigns page and rail. Read only."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_view_service import CampaignViewService
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class CampaignListTool(Tool):
    name = "campaign_list"
    label = "List campaigns"
    plugin = PLUGIN
    description = (
        "List every campaign: product, the gate it waits at, cast member, spend, and whether this "
        "chat started it. Read only."
    )
    parameters = {
        "type": "object",
        "properties": {
            "session": {"type": "string", "description": "A chat's session key; its campaigns are marked `mine`."},
        },
    }

    def __init__(self, views: CampaignViewService, workspace: RunWorkspace) -> None:
        self._views = views
        self._ws = workspace

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            rows = self._views.campaigns(str(params.get("session") or ""))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"campaign_list: {type(e).__name__}: {e}", is_error=True)
        lines = [f"{r['id']} · {r['name']} · waits at {r['gate']} · ${r['spent_usd']:.2f}" for r in rows]
        return ToolResult.text(
            "\n".join(lines) or "no campaigns yet",
            # `root` lets the window turn the workspace paths into files it can fetch.
            details={"root": str(self._ws.root()), "campaigns": rows},
        )
