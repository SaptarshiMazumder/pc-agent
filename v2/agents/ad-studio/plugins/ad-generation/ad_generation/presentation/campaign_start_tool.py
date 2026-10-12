"""campaign_start — product photos in: read the product, copy its recipe's steps, write the brief."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_run_context
from agent_runtime.application.tool_models import brain_model, resolve_tool_model

from ad_generation.application.campaign_view_service import CampaignViewService
from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.presentation.campaign_text import CampaignText
from ad_generation.presentation.creative_direction_params import CreativeDirectionParams
from ad_generation.presentation.generation_backend_resolver import PLUGIN, plugin_setting
from ad_generation.presentation.tool_progress_reporter import ToolProgressReporter


class CampaignStartTool(Tool):
    name = "campaign_start"
    label = "Start an ad"
    plugin = PLUGIN
    needs_model = True
    model_kind = "vision"
    default_timeout_sec = 900.0
    description = (
        "Start an ad from product photos: reads the product, gives the campaign its recipe's steps "
        "(a checklist, e.g. brief -> stills -> clip) and runs the first, the brief. Then show the "
        "brief and stop: the user runs each next step (step_run), re-runs any step, or adds their own."
    )
    parameters = {
        "type": "object",
        "required": ["name", "photos"],
        "properties": {
            "name": {"type": "string", "description": "The product's name."},
            "photos": {"type": "array", "items": {"type": "string"}, "description": "Workspace paths of the product photos (1-4)."},
            "stills": {"type": "array", "items": {"type": "string"}, "description": "Workspace paths of the user's OWN stills, used as they are in the images stage (nothing generated there; the clip starts from them). Only files the user said are stills to use."},
            "references": {"type": "array", "items": {"type": "string"}, "description": "Workspace paths of images the user wants followed (a look, pose, background) — added to the stills' references."},
            "cast": {"type": "string", "description": "The cast member to use; empty lets the brief pick from the cast."},
            "recipe": {"type": "string", "description": "The template (recipe) to use; empty uses the one the product matches."},
            "settings": {"type": "object", "description": "The user's choices for the brief's settings, by key (aspect_ratio, location, time_of_day, mood…) — only what the user chose."},
            "auto": {"type": "boolean", "description": "Only when the user asked for Auto: let the agent pick what they did not choose. Default off."},
            **CreativeDirectionParams.SCHEMA,
        },
    }

    def __init__(self, config, service_factory, views: CampaignViewService, images: VisionImagePreparer) -> None:
        self.config = config
        self._service_factory = service_factory
        self._views = views
        self._images = images

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            name = str(params.get("name") or "").strip()
            photos = [str(p) for p in params.get("photos") or []]
            if not name or not photos:
                raise ValueError("give the product's name and its photos")

            def reasoner(tool: str, kind: str) -> ModelAccessReasoner:
                model = resolve_tool_model(self.config, PLUGIN, tool, kind=kind) or brain_model(self.config)
                return ModelAccessReasoner(self.models, model, self._images, timeout_s=150)

            service = self._service_factory(
                reasoner("product_analyze", "vision"),
                reasoner("campaign_brief", "text"),
                reasoner("media_check", "vision"),
                ToolProgressReporter(asyncio.get_running_loop(), on_update),
            )
            campaign_id, _ = await asyncio.to_thread(
                service.start,
                name,
                photos,
                str(params.get("cast") or ""),
                CreativeDirectionParams.parse(params),
                str(params.get("recipe") or ""),
                str(getattr(current_run_context(), "session_key", "") or ""),
                str(plugin_setting(self.config, "generation_approval", "ask")),
                bool(params["auto"]) if "auto" in params else None,
                dict(params.get("settings") or {}),
                [str(p) for p in params.get("stills") or []],
                [str(p) for p in params.get("references") or []],
            )
            detail = self._views.detail(campaign_id)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"campaign_start: {type(e).__name__}: {e}", is_error=True)
        brief = next((s for s in detail["steps"] if s["action"] == "brief"), None)
        waiting = [s["label"] for s in (brief or {}).get("settings", []) if s["missing"]] if brief and brief["status"] == "todo" else []
        if waiting:
            return ToolResult.text(
                f"campaign {campaign_id} is set up and waiting for the user's choices before the brief: {', '.join(waiting)}. "
                "They choose them in the studio (or say them); nothing was written or paid yet.",
                details={"campaign": detail},
            )
        return CampaignText.result(detail, "brief", [])
