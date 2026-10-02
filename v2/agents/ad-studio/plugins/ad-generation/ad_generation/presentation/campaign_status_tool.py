"""campaign_status — one campaign as the window draws it: gate, brief, sheet, stills, clips. Read only."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_view_service import CampaignViewService
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class CampaignStatusTool(Tool):
    name = "campaign_status"
    label = "Campaign status"
    plugin = PLUGIN
    description = (
        "One campaign's state: the gate it waits at, the brief, the shoot sheet, each shot's "
        "passing stills with scores, the chosen still, the clip, and the spend. Read only."
    )
    parameters = {
        "type": "object",
        "required": ["campaign"],
        "properties": {"campaign": {"type": "string"}},
    }

    def __init__(self, views: CampaignViewService, workspace: RunWorkspace) -> None:
        self._views = views
        self._ws = workspace

    async def execute(self, tool_call_id, params, abort, on_update=None):
        campaign = str(params.get("campaign") or "")
        try:
            detail = self._views.detail(campaign)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"campaign_status: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            f"{campaign} · {detail['product']['name']} · waits at {detail['gate']} · ${detail['spent_usd']:.2f}",
            details={"root": str(self._ws.root()), "campaign": detail},
        )
