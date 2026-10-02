"""product_analyze — the first step: product photos -> a campaign and the product's profile."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer


class ProductAnalyzeTool(Tool):
    name = "product_analyze"
    label = "Read the product"
    plugin = "ad-generation"
    needs_model = True
    model_kind = "vision"
    default_timeout_sec = 180.0
    description = (
        "Start a campaign from product photos: reads what the product is, its materials and "
        "colours, who it is for, and the details every generated image must keep exact (logo, "
        "hardware, stitching, print). Returns the campaign id every later step takes."
    )
    parameters = {
        "type": "object",
        "required": ["name", "photos"],
        "properties": {
            "name": {"type": "string", "description": "The product's name, e.g. 'Linea leather tote'."},
            "photos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Workspace paths of the product photos (1-4, clearest first).",
            },
            "notes": {"type": "string", "description": "Anything the user said about it: brand, price, store, audience."},
        },
    }

    def __init__(self, config, service_factory, images: VisionImagePreparer) -> None:
        self.config = config
        self._service_factory = service_factory
        self._images = images

    async def execute(self, tool_call_id, params, abort, on_update=None):
        reasoner = ModelAccessReasoner(self.models, self.resolve_model(self.config), self._images, timeout_s=150)
        try:
            campaign_id, profile = await asyncio.to_thread(
                self._service_factory(reasoner).analyze,
                str(params.get("name") or "").strip(),
                [str(p) for p in params.get("photos") or []],
                str(params.get("notes") or ""),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"product_analyze: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            f"campaign {campaign_id}: {profile.name} — {profile.category}\n"
            f"{profile.description}\n"
            "must stay exact: " + "; ".join(profile.must_keep) + "\n"
            f"audience: {profile.audience} · tier: {profile.price_tier}\n"
            "Next: campaign_brief.",
            details={"campaign": campaign_id, "profile": profile.to_dict()},
        )
