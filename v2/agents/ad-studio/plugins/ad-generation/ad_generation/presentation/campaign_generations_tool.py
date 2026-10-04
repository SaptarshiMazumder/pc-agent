"""campaign_generations — everything a campaign made, for the window's Generations tab. Read only."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_view_service import CampaignViewService
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class CampaignGenerationsTool(Tool):
    name = "campaign_generations"
    label = "Campaign generations"
    plugin = PLUGIN
    description = (
        "Everything a campaign made, newest first: each sheet, still and clip with its stage and "
        "shot, the model that made it, its cost, its check (score, pass, reasons) and whether it "
        "is in use. Read only."
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
            rows = self._views.generations(campaign)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"campaign_generations: {type(e).__name__}: {e}", is_error=True)
        total = sum(r["cost_usd"] for r in rows)
        return ToolResult.text(
            f"{campaign}: {len(rows)} generation(s), ${total:.2f}",
            details={"root": str(self._ws.root()), "generations": rows},
        )
