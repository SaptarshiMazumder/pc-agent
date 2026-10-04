"""step_run — run any step of a campaign, again and again; every run adds to the step."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.tool_models import brain_model, resolve_tool_model

from ad_generation.application.campaign_checklist_loader import CampaignChecklistLoader
from ad_generation.application.campaign_view_service import CampaignViewService
from ad_generation.application.interfaces.media_previewer import MediaPreviewer
from ad_generation.application.run_approvals import RunApprovals
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.step_run import StepRun
from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.presentation.campaign_text import CampaignText
from ad_generation.presentation.creative_direction_params import CreativeDirectionParams
from ad_generation.presentation.generation_backend_resolver import PLUGIN, GenerationBackendResolver
from ad_generation.presentation.generation_choice_validator import GenerationChoiceValidator
from ad_generation.presentation.run_gate import gate
from ad_generation.presentation.tool_progress_reporter import ToolProgressReporter


class StepRunTool(Tool):
    name = "step_run"
    label = "Run a step"
    plugin = PLUGIN
    needs_model = True
    model_kind = "vision"
    default_timeout_sec = 1800.0
    description = (
        "Run one step of a campaign — any step, at any time, as many times as the user wants; each "
        "run ADDS results to the step and changes nothing else. brief: rewrite it (`change`, and any "
        "direction field the user changed — a text ad's new `headline`, a new `location` — which replaces the old). "
        "sheet: a new shoot sheet (`change`). images: `count` images from the step's prompt and "
        "references, or the user's own `prompt` / `references` / `like`. video: a clip from the "
        "source step's picked image, or `first_frame`, with `prompt`, `model`, `seconds`, "
        "`resolution`. Leave out what the user did not say — the step's last settings and defaults "
        "fill it in."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "step"],
        "properties": {
            "campaign": {"type": "string"},
            "step": {"type": "string", "description": "The step id, e.g. 'stills'."},
            "change": {"type": "string", "description": "What to change, in the user's words."},
            "prompt": {"type": "string", "description": "images / video: the whole prompt, replacing the step's."},
            "references": {"type": "array", "items": {"type": "string"}, "description": "images / video: exactly these reference images (workspace paths), replacing the step's."},
            "count": {"type": "integer", "description": "images: how many (1-4)."},
            **{k: {**v, "description": "brief: " + v["description"]} for k, v in CreativeDirectionParams.SCHEMA.items() if k != "direction"},
            "like": {"type": "string", "description": "images: an image to make more like — it rides as the composition to vary."},
            "model": {"type": "string", "description": "provider/model (generation_models lists them)."},
            "first_frame": {"type": "string", "description": "video: the image it starts from; default: the source step's pick."},
            "seconds": {"type": "integer", "description": "video: clip length, within the model's range."},
            "resolution": {"type": "string", "description": "video: one the model offers (480p, 720p, 1080p, 4K…)."},
            "budget_usd": {"type": "number", "description": "Raise the campaign's budget (only when the user says)."},
            "approval": {"type": "string", "description": "The studio's approval for this run — copy it exactly from the user's message; never invent one."},
        },
    }

    def __init__(
        self,
        config,
        service_factory,
        loader: CampaignChecklistLoader,
        views: CampaignViewService,
        validator: GenerationChoiceValidator,
        previews: MediaPreviewer,
        approvals: RunApprovals,
        images: VisionImagePreparer,
    ) -> None:
        self.config = config
        self._service_factory = service_factory
        self._loader = loader
        self._views = views
        self._validator = validator
        self._previews = previews
        self._approvals = approvals
        self._images = images

    def _run(self, params: dict, step: CampaignStep, backends) -> StepRun:
        """The run the user asked for — with what the step will actually use (its last settings
        where the run says nothing) checked against the model's spec first."""
        model = str(params.get("model") or "").strip()
        seconds = int(params.get("seconds") or 0)
        resolution = str(params.get("resolution") or "").strip()
        references = params.get("references")
        if step.action in ("sheet", "product_sheet") and model:
            self._validator.image(model, step.action)
        if step.action == "images":
            # Checked against what the run will really send, before the user is asked to approve.
            effective = model or step.model or "/".join(backends.image)
            sent = list(references) if references is not None else self._defaults_of(params, step)
            like = str(params.get("like") or "").strip()
            self._validator.image(effective, step.action, len(sent) + (1 if like and like not in sent else 0))
        if step.action == "video":
            effective = model or step.model or "/".join(backends.video)
            resolution = resolution or self._validator.resolution_for(effective, step.resolution)
            self._validator.video(effective, seconds or step.seconds, resolution)
        count = int(params.get("count") or 0)
        if count and not 1 <= count <= 4:
            raise ValueError(f"count is 1-4, not {count}")
        return StepRun(
            change=str(params.get("change") or "").strip(),
            prompt=str(params.get("prompt") or "").strip(),
            references=tuple(str(r) for r in references) if references is not None else None,
            count=count,
            like=str(params.get("like") or "").strip(),
            model=model,
            first_frame=str(params.get("first_frame") or "").strip(),
            seconds=seconds,
            resolution=resolution,
            direction={k: v for k, v in CreativeDirectionParams.parse(params).given().items() if k != "notes"}
            if step.action == "brief"
            else {},
        )

    def _defaults_of(self, params: dict, step: CampaignStep) -> list[str]:
        """The references the step sends when the run gives none (as the window shows them)."""
        detail = self._views.detail(str(params.get("campaign") or ""))
        return list(next(s for s in detail["steps"] if s["id"] == step.id)["defaults"]["references"])

    async def execute(self, tool_call_id, params, abort, on_update=None):
        campaign = str(params.get("campaign") or "")
        step_id = str(params.get("step") or "")
        try:
            backends = GenerationBackendResolver(self.config).backends()
            step = self._loader.load(campaign).step(step_id)
            run = self._run(params, step, backends)
            if step.action != "brief":  # a brief is text: nothing to approve
                default = backends.video if step.action == "video" else backends.image
                model = run.model or step.model or "/".join(default)
                pinned = {"model": model, "count": run.count, "seconds": run.seconds, "resolution": run.resolution,
                          "first_frame": run.first_frame}
                proposal = {k: v for k, v in params.items() if k not in ("campaign", "step", "approval")}
                stopped = gate(self._approvals, campaign, step_id, self.name, pinned,
                               str(params.get("approval") or ""), {**proposal, "model": model})
                if stopped is not None:
                    return stopped

            def reasoner(tool: str, kind: str) -> ModelAccessReasoner:
                model = resolve_tool_model(self.config, PLUGIN, tool, kind=kind) or brain_model(self.config)
                return ModelAccessReasoner(self.models, model, self._images, timeout_s=150)

            service = self._service_factory(
                reasoner("product_analyze", "vision"),
                reasoner("campaign_brief", "text"),
                reasoner("media_check", "vision"),
                ToolProgressReporter(asyncio.get_running_loop(), on_update),
            )
            made, _ = await asyncio.to_thread(
                service.run, campaign, step_id, run, backends, float(params.get("budget_usd") or 0)
            )
            detail = self._views.detail(campaign)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"step_run: {type(e).__name__}: {e}", is_error=True)
        attach = [self._previews.preview(m.path) if m.kind == "image" else m.path for m in made]
        return CampaignText.result(detail, step_id, [m.path for m in made], attach)
