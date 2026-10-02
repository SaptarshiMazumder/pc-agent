"""media_check — the quality gate between a still and its clip, and after the clip."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer


class MediaCheckTool(Tool):
    name = "media_check"
    label = "Check a still or clip"
    plugin = "ad-generation"
    needs_model = True
    model_kind = "vision"
    default_timeout_sec = 180.0
    description = (
        "Judge a still (or a clip, by its last frame) against the product photos and the cast "
        "member's sheet: is every must-keep detail of the product exact, is the person the same, "
        "and would it stop a scroll. Returns pass/fail, a score and the concrete problems — "
        "which become the next attempt's `correction`."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "shot", "path"],
        "properties": {
            "campaign": {"type": "string"},
            "shot": {"type": "string"},
            "path": {"type": "string", "description": "The still or clip to judge."},
            "last_frame": {"type": "string", "description": "For a clip: the last-frame image shot_animate returned."},
        },
    }

    def __init__(self, config, service_factory, images: VisionImagePreparer) -> None:
        self.config = config
        self._service_factory = service_factory
        self._images = images

    async def execute(self, tool_call_id, params, abort, on_update=None):
        reasoner = ModelAccessReasoner(self.models, self.resolve_model(self.config), self._images, timeout_s=150)
        try:
            verdict = await asyncio.to_thread(
                self._service_factory(reasoner).check,
                str(params.get("campaign") or ""),
                str(params.get("shot") or ""),
                str(params.get("path") or ""),
                str(params.get("last_frame") or ""),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"media_check: {type(e).__name__}: {e}", is_error=True)
        head = "PASS" if verdict.passed else "FAIL"
        lines = [
            f"{head} {verdict.path} · score {verdict.score}/10 · product exact: {verdict.product_exact} · "
            f"same person: {verdict.identity_kept}"
        ]
        lines += [f"- {p}" for p in verdict.problems]
        return ToolResult.text("\n".join(lines), details={"verdict": verdict.to_dict()})
