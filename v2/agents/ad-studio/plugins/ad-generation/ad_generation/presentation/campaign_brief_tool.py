"""campaign_brief — the ad's plan: format, concept, hook, and its shots with their prompts."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.tool_models import brain_model

from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.presentation.creative_direction_params import CreativeDirectionParams
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer


class CampaignBriefTool(Tool):
    name = "campaign_brief"
    label = "Write the brief"
    plugin = "ad-generation"
    needs_model = True
    model_kind = "text"
    default_timeout_sec = 180.0
    description = (
        "Write the ad's brief for a campaign: picks a proven format (or uses the one named), a "
        "concept and a scroll-stopping hook, and 3-5 shots — each with the prompt for its still "
        "and the motion for its clip. Casts only people from the cast. Rewrites the brief when "
        "called again."
    )
    parameters = {
        "type": "object",
        "required": ["campaign"],
        "properties": {
            "campaign": {"type": "string", "description": "The campaign id product_analyze returned."},
            "format": {"type": "string", "description": "An ad format key; empty lets the writer pick the best fit."},
            "cast": {"type": "array", "items": {"type": "string"}, "description": "Cast member names to use; empty = any."},
            "shots": {"type": "integer", "description": "How many shots (default 3)."},
            **CreativeDirectionParams.SCHEMA,
        },
    }

    def __init__(self, config, service_factory, images: VisionImagePreparer) -> None:
        self.config = config
        self._service_factory = service_factory
        self._images = images

    async def execute(self, tool_call_id, params, abort, on_update=None):
        model = self.resolve_model(self.config) or brain_model(self.config)
        reasoner = ModelAccessReasoner(self.models, model, self._images, timeout_s=150)
        try:
            brief = await asyncio.to_thread(
                self._service_factory(reasoner).write,
                str(params.get("campaign") or ""),
                str(params.get("format") or ""),
                [str(c) for c in params.get("cast") or []],
                int(params.get("shots") or 3),
                CreativeDirectionParams.parse(params),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"campaign_brief: {type(e).__name__}: {e}", is_error=True)
        lines = [
            f"format: {brief.format_key} · {brief.aspect_ratio}",
            f"concept: {brief.concept}",
            f"hook: {brief.hook}",
            "look: " + "; ".join(f"{k}: {v}" for k, v in brief.look.items() if v),
        ]
        for s in brief.shots:
            who = ", ".join(s.cast) or "product only"
            lines.append(f"{s.id} ({s.purpose}, {s.duration_s}s, {who}): {s.keyframe_prompt}\n   motion: {s.motion_prompt}")
        lines.append("Next: keyframe_generate for each shot.")
        return ToolResult.text("\n".join(lines), details={"brief": brief.to_dict()})
