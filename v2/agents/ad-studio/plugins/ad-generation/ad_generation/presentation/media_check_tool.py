"""media_check — the checker's opinion of one image or clip: advice for the user, never a gate."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_checklist_loader import CampaignChecklistLoader
from ad_generation.application.interfaces.clip_frames import ClipFrames
from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class MediaCheckTool(Tool):
    name = "media_check"
    label = "Check an image or clip"
    plugin = PLUGIN
    needs_model = True
    model_kind = "vision"
    default_timeout_sec = 180.0
    description = (
        "Judge an image (or a clip, by its last frame) against the product photos and the cast "
        "member's sheet: is every must-keep detail of the product exact, is the person the same, "
        "would it stop a scroll. A score and the concrete problems — advice to show the user. "
        "Every step's results are already checked; use this for anything else."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "step", "path"],
        "properties": {
            "campaign": {"type": "string"},
            "step": {"type": "string", "description": "The step it belongs to (who and what should appear)."},
            "path": {"type": "string", "description": "The image or clip to judge."},
        },
    }

    def __init__(self, config, service_factory, loader: CampaignChecklistLoader, frames: ClipFrames, images: VisionImagePreparer) -> None:
        self.config = config
        self._service_factory = service_factory
        self._loader = loader
        self._frames = frames
        self._images = images

    def _check(self, reasoner, campaign: str, step_id: str, path: str):
        checklist = self._loader.load(campaign)
        step = checklist.step(step_id)
        last = self._frames.last_frame(path) if path.lower().endswith((".mp4", ".mov", ".webm")) else ""
        cast = (checklist.cast_name,) if step.cast and checklist.cast_name else ()
        return self._service_factory(reasoner).check(campaign, path, last, step.title, cast, step.shows_product)

    async def execute(self, tool_call_id, params, abort, on_update=None):
        reasoner = ModelAccessReasoner(self.models, self.resolve_model(self.config), self._images, timeout_s=150)
        try:
            verdict = await asyncio.to_thread(
                self._check,
                reasoner,
                str(params.get("campaign") or ""),
                str(params.get("step") or ""),
                str(params.get("path") or ""),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"media_check: {type(e).__name__}: {e}", is_error=True)
        lines = [
            f"{verdict.path} · score {verdict.score}/10 · product exact: {verdict.product_exact} · "
            f"same person: {verdict.identity_kept}"
        ]
        lines += [f"- {p}" for p in verdict.problems]
        return ToolResult.text("\n".join(lines), details={"verdict": verdict.to_dict()})
