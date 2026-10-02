"""still_fix — one still, one change the user asked for, edited in place by an image-editing model."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.tool_models import brain_model, resolve_tool_model

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.presentation.generation_backend_resolver import PLUGIN, GenerationBackendResolver


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
        "is the shot's next take, checked like any still; when it passes and the campaign waits at "
        "the stills gate, it is listed with the shot's passing stills for the user to pick. Show it "
        "and let the user decide; never pick it for them."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "shot", "still", "change"],
        "properties": {
            "campaign": {"type": "string"},
            "shot": {"type": "string", "description": "The shot id, e.g. 's1'."},
            "still": {"type": "string", "description": "Workspace path of the still to edit."},
            "change": {"type": "string", "description": "What to change, in the user's words. Only this changes."},
            "provider": {"type": "string", "description": "Override the configured image-editing provider."},
            "model": {"type": "string", "description": "Override the configured image-editing model."},
        },
    }

    def __init__(self, config, service_factory, store: CampaignStore, images: VisionImagePreparer) -> None:
        self.config = config
        self._service_factory = service_factory
        self._store = store
        self._images = images

    async def execute(self, tool_call_id, params, abort, on_update=None):
        campaign = str(params.get("campaign") or "")
        try:
            provider, model = GenerationBackendResolver(self.config).resolve(
                self.name, str(params.get("provider") or ""), str(params.get("model") or "")
            )
            check_model = resolve_tool_model(self.config, PLUGIN, "media_check", kind="vision") or brain_model(self.config)
            service = self._service_factory(ModelAccessReasoner(self.models, check_model, self._images, timeout_s=150))
            fixed, verdict = await asyncio.to_thread(
                service.fix,
                campaign,
                str(params.get("shot") or ""),
                str(params.get("still") or ""),
                str(params.get("change") or ""),
                provider,
                model,
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
            "Show it to the user. "
            + (
                "It is listed with the shot's passing stills; at the stills gate they pick it with `picks`."
                if verdict.passed
                else "It did not pass, so it cannot be animated; say why, and ask whether to try a different change."
            ),
        ]
        return ToolResult.text(
            "\n".join(lines),
            details={"still": fixed.to_dict(), "verdict": verdict.to_dict(), "campaign_spent": spent},
            artifacts=[fixed.path],
        )
