"""keyframe_generate — a shot's stills: cast member + product in the shot's scene."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.tool_models import tool_config

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.keyframe_service import KeyframeService
from ad_generation.presentation.generation_backend_resolver import PLUGIN, GenerationBackendResolver


class KeyframeGenerateTool(Tool):
    name = "keyframe_generate"
    label = "Generate a shot's stills"
    plugin = PLUGIN
    default_timeout_sec = 600.0
    description = (
        "Generate the stills for one shot of the brief, referencing the cast member's character "
        "sheet and the product photos. Several variants per call; check each with media_check "
        "and animate only a passing one. Pass `correction` to fix what a check found."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "shot"],
        "properties": {
            "campaign": {"type": "string"},
            "shot": {"type": "string", "description": "The shot id from the brief, e.g. 's1'."},
            "variants": {"type": "integer", "description": "How many stills (default from config)."},
            "correction": {"type": "string", "description": "What to fix from the last attempt, from media_check."},
            "provider": {"type": "string", "description": "Override the configured image provider."},
            "model": {"type": "string", "description": "Override the configured image model."},
        },
    }

    def __init__(self, config, service: KeyframeService, store: CampaignStore) -> None:
        self.config = config
        self._service = service
        self._store = store

    async def execute(self, tool_call_id, params, abort, on_update=None):
        campaign = str(params.get("campaign") or "")
        try:
            provider, model = GenerationBackendResolver(self.config).resolve(
                self.name, str(params.get("provider") or ""), str(params.get("model") or "")
            )
            variants = int(params.get("variants") or tool_config(self.config, PLUGIN, self.name, "variants", 2))
            stills = await asyncio.to_thread(
                self._service.generate,
                campaign,
                str(params.get("shot") or ""),
                variants,
                str(params.get("correction") or ""),
                provider,
                model,
            )
            spent = self._store.spent(campaign)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"keyframe_generate: {type(e).__name__}: {e}", is_error=True)
        cost = sum(s.cost_usd for s in stills)
        return ToolResult.text(
            "\n".join(s.path for s in stills)
            + f"\n{len(stills)} still(s) · {provider} {model} · ${cost:.3f} · campaign so far ${spent:.2f}"
            "\nNext: media_check each; animate a passing one.",
            details={"stills": [s.to_dict() for s in stills], "campaign_spent": spent},
            artifacts=[s.path for s in stills],
        )
