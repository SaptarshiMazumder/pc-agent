"""shot_animate — a passing still becomes the shot's clip."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.tool_models import tool_config

from ad_generation.application.animation_service import AnimationService
from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.presentation.generation_backend_resolver import PLUGIN, GenerationBackendResolver


class ShotAnimateTool(Tool):
    name = "shot_animate"
    label = "Animate a shot"
    plugin = PLUGIN
    # A 1080p clip can queue and render for many minutes; the call holds until it is downloaded.
    default_timeout_sec = 1500.0
    description = (
        "Animate a shot: the chosen still is the clip's first frame (the clip takes its shape), "
        "the brief's motion prompt drives it. Only animate a still media_check passed. Returns "
        "the clip, its last frame (for media_check) and what it cost."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "shot", "still"],
        "properties": {
            "campaign": {"type": "string"},
            "shot": {"type": "string"},
            "still": {"type": "string", "description": "The passing still, from keyframe_generate."},
            "resolution": {"type": "string", "enum": ["480p", "720p", "1080p"], "description": "Default from config."},
            "audio": {"type": "boolean", "description": "Generate sound (default from config)."},
            "correction": {"type": "string", "description": "What to fix from the last clip."},
            "provider": {"type": "string", "description": "Override the configured video provider."},
            "model": {"type": "string", "description": "Override the configured video model."},
        },
    }

    def __init__(self, config, service: AnimationService, store: CampaignStore) -> None:
        self.config = config
        self._service = service
        self._store = store

    async def execute(self, tool_call_id, params, abort, on_update=None):
        campaign = str(params.get("campaign") or "")
        try:
            provider, model = GenerationBackendResolver(self.config).resolve(
                self.name, str(params.get("provider") or ""), str(params.get("model") or "")
            )
            resolution = str(params.get("resolution") or tool_config(self.config, PLUGIN, self.name, "resolution", "720p"))
            audio = params["audio"] if "audio" in params else bool(tool_config(self.config, PLUGIN, self.name, "audio", False))
            clip = await asyncio.to_thread(
                self._service.animate,
                campaign,
                str(params.get("shot") or ""),
                str(params.get("still") or ""),
                resolution,
                bool(audio),
                str(params.get("correction") or ""),
                provider,
                model,
            )
            spent = self._store.spent(campaign)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"shot_animate: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            f"{clip.path}"
            + (f"\nlast frame: {clip.last_frame}" if clip.last_frame else "")
            + f"\n{provider} {model} · {resolution} · ${clip.cost_usd:.3f} ({clip.cost_basis}) · campaign so far ${spent:.2f}",
            details={"clip": clip.to_dict(), "campaign_spent": spent},
            artifacts=[clip.path],
        )
