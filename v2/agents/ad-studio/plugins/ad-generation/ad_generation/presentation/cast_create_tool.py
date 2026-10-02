"""cast_create — a recurring AI model and their character sheet."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.cast_service import CastService
from ad_generation.presentation.generation_backend_resolver import GenerationBackendResolver


class CastCreateTool(Tool):
    name = "cast_create"
    label = "Create a cast member"
    plugin = "ad-generation"
    default_timeout_sec = 300.0
    description = (
        "Create a recurring AI model for the account: generates their character sheet (front, "
        "three-quarter and side views, full body and face close-up) from a description. Every "
        "keyframe that casts them references this sheet, so they look the same in every ad. "
        "Never a real or famous person."
    )
    parameters = {
        "type": "object",
        "required": ["name", "description"],
        "properties": {
            "name": {"type": "string", "description": "Lowercase handle, e.g. 'mira'."},
            "description": {
                "type": "string",
                "description": "Face, hair, skin, build, age range, style and default outfit — specific.",
            },
            "references": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional workspace images for the look (style, not a real person's face).",
            },
            "provider": {"type": "string", "description": "Override the configured image provider."},
            "model": {"type": "string", "description": "Override the configured image model."},
        },
    }

    def __init__(self, config, service: CastService) -> None:
        self.config = config
        self._service = service

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            provider, model = GenerationBackendResolver(self.config).resolve(
                self.name, str(params.get("provider") or ""), str(params.get("model") or "")
            )
            member, sheet = await asyncio.to_thread(
                self._service.create,
                str(params.get("name") or "").strip(),
                str(params.get("description") or "").strip(),
                [str(r) for r in params.get("references") or []],
                provider,
                model,
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"cast_create: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            f"cast member {member.name}: sheet {member.sheet} ({sheet.provider} {sheet.model}, ${sheet.cost_usd:.3f}). "
            "Show it to the user — this face is the account's from now on.",
            details={"member": member.to_dict(), "media": sheet.to_dict()},
            artifacts=[member.sheet],
        )
