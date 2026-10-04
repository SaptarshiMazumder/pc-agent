"""clip_edit — change a clip the user selected, or continue it."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.tool_models import brain_model, resolve_tool_model

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.application.run_approvals import RunApprovals
from ad_generation.presentation.generation_backend_resolver import PLUGIN, VIDEO, GenerationBackendResolver
from ad_generation.presentation.run_gate import gate


class ClipEditTool(Tool):
    name = "clip_edit"
    label = "Edit or extend a clip"
    plugin = PLUGIN
    needs_model = True
    model_kind = "vision"
    default_timeout_sec = 1200.0
    description = (
        "Change ONE clip the user selected. mode 'edit': the same clip with one thing changed "
        "('make it golden hour', 'change the wall to brick'), on a model that edits video. mode "
        "'extend': the clip continued by `seconds` with what happens next — natively on a model that "
        "extends (Seedance 2.5), otherwise as a new clip that starts on the source's last frame. The "
        "result is added to the step's results and checked (advice); the source clip stays in the "
        "campaign's generations. Show it and let the user decide."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "step", "clip", "mode", "change"],
        "properties": {
            "campaign": {"type": "string"},
            "step": {"type": "string", "description": "The step the clip belongs to (the result is added to it), e.g. 'clip'."},
            "clip": {"type": "string", "description": "Workspace path of the selected clip (.mp4)."},
            "mode": {"type": "string", "enum": ["edit", "extend"]},
            "change": {
                "type": "string",
                "description": "edit: what to change. extend: what happens next. In the user's words.",
            },
            "seconds": {"type": "integer", "description": "extend: how many seconds to add (default 5)."},
            "provider": {"type": "string", "description": "Override the configured provider."},
            "model": {"type": "string", "description": "Override the configured model."},
            "approval": {"type": "string", "description": "The studio's approval for this run — copy it exactly from the user's message; never invent one."},
        },
    }

    def __init__(
        self, config, service_factory, store: CampaignStore, approvals: RunApprovals, images: VisionImagePreparer
    ) -> None:
        self.config = config
        self._service_factory = service_factory
        self._store = store
        self._approvals = approvals
        self._images = images

    async def execute(self, tool_call_id, params, abort, on_update=None):
        campaign = str(params.get("campaign") or "")
        mode = str(params.get("mode") or "")
        try:
            if mode not in ("edit", "extend"):
                raise ValueError(f"mode must be 'edit' or 'extend', not '{mode}'")
            # extend defaults to the clip model; edit to the clip-editing model.
            tool = self.name if mode == "edit" else VIDEO
            provider, model = GenerationBackendResolver(self.config).resolve(
                tool, str(params.get("provider") or ""), str(params.get("model") or "")
            )
            seconds_asked = int(params.get("seconds") or 5) if mode == "extend" else 0
            stopped = gate(
                self._approvals, campaign, str(params.get("step") or ""), self.name,
                {"model": f"{provider}/{model}", "clip": str(params.get("clip") or ""), "mode": mode, "seconds": seconds_asked},
                str(params.get("approval") or ""),
                {"clip": str(params.get("clip") or ""), "mode": mode, "change": str(params.get("change") or ""),
                 "seconds": seconds_asked, "provider": provider, "model": model},
            )
            if stopped is not None:
                return stopped
            check_model = resolve_tool_model(self.config, PLUGIN, "media_check", kind="vision") or brain_model(self.config)
            service = self._service_factory(ModelAccessReasoner(self.models, check_model, self._images, timeout_s=150))
            step_id, clip, change = str(params.get("step") or ""), str(params.get("clip") or ""), str(params.get("change") or "")
            if mode == "edit":
                made, verdict = await asyncio.to_thread(service.edit, campaign, step_id, clip, change, provider, model)
            else:
                seconds = int(params.get("seconds") or 5)
                made, verdict = await asyncio.to_thread(
                    service.extend, campaign, step_id, clip, change, seconds, provider, model
                )
            spent = self._store.spent(campaign)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"clip_edit: {type(e).__name__}: {e}", is_error=True)
        head = "PASS" if verdict.passed else "FAIL"
        lines = [
            f"{made.path} · {mode} of {clip} · {provider} {model} · ${made.cost_usd:.3f} · campaign so far ${spent:.2f}",
            f"{head} · score {verdict.score}/10 · product {'exact' if verdict.product_exact else 'NOT exact'} · "
            f"identity {'kept' if verdict.identity_kept else 'NOT kept'}",
            *[f"   - {p}" for p in verdict.problems],
            "It is in the step's results; the user picks it if they want it. The source clip stays.",
        ]
        return ToolResult.text(
            "\n".join(lines),
            details={"clip": made.to_dict(), "verdict": verdict.to_dict(), "campaign_spent": spent},
            artifacts=[made.path],
        )
