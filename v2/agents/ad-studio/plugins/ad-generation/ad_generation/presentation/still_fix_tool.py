"""still_fix — one still, one change the user asked for, edited in place by an image-editing model."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.tool_models import brain_model, resolve_tool_model

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.media_previewer import MediaPreviewer
from ad_generation.application.run_approvals import RunApprovals
from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.presentation.generation_backend_resolver import PLUGIN, GenerationBackendResolver
from ad_generation.presentation.run_gate import gate


class StillFixTool(Tool):
    name = "still_fix"
    label = "Fix a still"
    plugin = PLUGIN
    needs_model = True
    model_kind = "vision"
    default_timeout_sec = 600.0
    description = (
        "Edit ONE still the user picked, changing only what they said ('fix the COACH lettering', "
        "'remove the extra ring on the strap'); everything else stays. The product photos and the "
        "cast sheet ride along so the fix matches the real product and keeps the face. The result "
        "is added to the step's results and checked (advice) — never picked for the user. Show it "
        "and let the user decide; never pick it for them."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "step", "still", "change"],
        "properties": {
            "campaign": {"type": "string"},
            "step": {"type": "string", "description": "The step the image belongs to (the fix is added to it), e.g. 'stills'."},
            "still": {"type": "string", "description": "Workspace path of the still to edit."},
            "change": {"type": "string", "description": "What to change, in the user's words. Only this changes."},
            "provider": {"type": "string", "description": "Override the configured image-editing provider."},
            "model": {"type": "string", "description": "Override the configured image-editing model."},
            "text": {"type": "array", "items": {"type": "string"}, "description": "A text ad: every line of text the design should carry after the fix, exactly — read back by the check."},
            "approval": {"type": "string", "description": "The studio's approval for this run — copy it exactly from the user's message; never invent one."},
        },
    }

    def __init__(
        self,
        config,
        service_factory,
        store: CampaignStore,
        previews: MediaPreviewer,
        approvals: RunApprovals,
        images: VisionImagePreparer,
    ) -> None:
        self.config = config
        self._service_factory = service_factory
        self._store = store
        self._previews = previews
        self._approvals = approvals
        self._images = images

    async def execute(self, tool_call_id, params, abort, on_update=None):
        campaign = str(params.get("campaign") or "")
        try:
            provider, model = GenerationBackendResolver(self.config).resolve(
                self.name, str(params.get("provider") or ""), str(params.get("model") or "")
            )
            step_id, still = str(params.get("step") or ""), str(params.get("still") or "")
            stopped = gate(
                self._approvals, campaign, step_id, self.name, {"model": f"{provider}/{model}", "still": still},
                str(params.get("approval") or ""),
                {"still": still, "change": str(params.get("change") or ""), "provider": provider, "model": model,
                 **({"text": list(params["text"])} if params.get("text") else {})},
            )
            if stopped is not None:
                return stopped
            check_model = resolve_tool_model(self.config, PLUGIN, "media_check", kind="vision") or brain_model(self.config)
            service = self._service_factory(ModelAccessReasoner(self.models, check_model, self._images, timeout_s=150))
            fixed, verdict = await asyncio.to_thread(
                service.fix,
                campaign,
                str(params.get("step") or ""),
                str(params.get("still") or ""),
                str(params.get("change") or ""),
                provider,
                model,
                tuple(str(t) for t in params.get("text") or ()),
            )
            spent = self._store.spent(campaign)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"still_fix: {type(e).__name__}: {e}", is_error=True)
        head = "PASS" if verdict.passed else "FAIL"
        lines = [
            f"{fixed.path} · {provider} {model} · ${fixed.cost_usd:.3f} · campaign so far ${spent:.2f}",
            f"{head} · score {verdict.score}/10 · product {'exact' if verdict.product_exact else 'NOT exact'} · "
            f"identity {'kept' if verdict.identity_kept else 'NOT kept'}",
            *[f"   - {p}" for p in verdict.problems],
            "It is in the step's results; the user picks it if they want it.",
        ]
        return ToolResult.text(
            "\n".join(lines),
            details={"still": fixed.to_dict(), "verdict": verdict.to_dict(), "campaign_spent": spent},
            artifacts=[self._previews.preview(fixed.path)],
        )
