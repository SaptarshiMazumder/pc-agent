"""campaign_run — the default path: product photos in, the recipe run step by step, stopping at
each gate for the user's say."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_run_context
from agent_runtime.application.tool_models import brain_model, resolve_tool_model, tool_config

from ad_generation.domain.campaign_progress import BRIEF, CLIPS, DONE, SHEET, STILLS
from ad_generation.domain.campaign_report import CampaignReport
from ad_generation.domain.generation_backends import GenerationBackends
from ad_generation.domain.run_options import RunOptions
from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.presentation.creative_direction_params import CreativeDirectionParams
from ad_generation.presentation.generation_backend_resolver import PLUGIN, GenerationBackendResolver
from ad_generation.presentation.tool_progress_reporter import ToolProgressReporter

# What the agent does next, per gate — said in the result so it never has to remember.
_NEXT = {
    BRIEF: "Show the user the look and each shot's scene, call campaign_ask (keep, or what to change), end the turn.",
    SHEET: "show_files the shoot sheet, call campaign_ask (keep, or redo the sheet with changes), end the turn.",
    STILLS: "show_files every still listed, call campaign_ask (keep, pick another still per shot, or redo a shot with changes), end the turn.",
    CLIPS: "show_files each clip, call campaign_ask (keep, or redo a clip with changes), end the turn.",
    DONE: "The campaign is finished; give the user the deliverables.",
}


class CampaignRunTool(Tool):
    name = "campaign_run"
    label = "Make the ad"
    plugin = PLUGIN
    needs_model = True
    model_kind = "vision"
    # A step of three shots, each with checked stills or a checked clip, retries included.
    default_timeout_sec = 3600.0
    description = (
        "Make an ad from product photos, the same way every time for that kind of product, ONE "
        "STEP AT A TIME. Start with name + photos: it reads the product, picks its recipe and "
        "writes the brief, then stops at a gate. Each later call passes `campaign` and moves past "
        "the gate the user approved: brief -> [sheet: the cast member in the ad's outfit and light, "
        "recipes with shoot_sheet] -> stills (generated, checked, retried with fixes) -> "
        "clips (the chosen still of each shot animated and checked) -> done. At every gate, show "
        "the results, call campaign_ask and end the turn; a call made before the user answered is "
        "refused. This is the default for a new product; use the single-step tools only for "
        "changes outside the recipe."
    )
    parameters = {
        "type": "object",
        "properties": {
            # STARTING
            "name": {"type": "string", "description": "Starting: the product's name."},
            "photos": {"type": "array", "items": {"type": "string"}, "description": "Starting: workspace paths of the product photos (1-4)."},
            "cast": {"type": "string", "description": "Starting: a cast member to use; empty lets the brief pick from the cast."},
            **CreativeDirectionParams.SCHEMA,
            "recipe": {"type": "string", "description": "Starting: force a recipe; empty uses the one the product matches."},
            # WHAT THIS RUN MAKES — per run, never fixed in the recipe. Each one left out falls
            # back to the recipe's default.
            "resolution": {"type": "string", "enum": ["480p", "720p", "1080p"], "description": "Starting: clip resolution. 480p is draft quality at about half the price of 720p."},
            "shots": {"type": "array", "items": {"type": "string"}, "description": "Starting: which of the recipe's shots to make, e.g. [\"s1\",\"s3\"]. Left out = all."},
            "animate": {"type": "array", "items": {"type": "string"}, "description": "Starting: which of the made shots become clips, e.g. [\"s1\"]; [] = stills only. Left out = the recipe's."},
            "variants": {"type": "integer", "description": "Starting: stills per attempt per shot."},
            "gates": {"type": "array", "items": {"type": "string", "enum": ["brief", "stills", "clips"]}, "description": "Starting: where the run stops for the user's approval. [] runs straight through to the end - only when the user asked for that. Left out = the recipe's (all three)."},
            # CONTINUING
            "campaign": {"type": "string", "description": "Continuing: the campaign id, after the user answered the gate's ask."},
            "picks": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Continuing at the stills gate: the still the user chose per shot, e.g. {\"s2\": \"campaigns/x/stills/s2/take-01-2.jpg\"}."},
            "redo": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Continuing: re-run this gate's step with the user's changes, in their words — {\"brief\": \"...\"} at the brief gate, {\"sheet\": \"...\"} at the sheet gate, {\"s3\": \"...\"} per shot at the stills or clips gate. The campaign stays at the gate."},
            "budget_usd": {"type": "number", "description": "Stop before spending past this; default is the recipe's. Continuing: raises it."},
            "video": {"type": "string", "description": "The clip model, as provider/model (video_models lists them), e.g. \"higgsfield/seedance_2_5\". Starting or continuing; kept for the rest of the campaign. Left out = the configured default."},
        },
    }

    def __init__(self, config, run_factory, images: VisionImagePreparer, specs: ModelSpecBook) -> None:
        self.config = config
        self._run_factory = run_factory
        self._images = images
        self._specs = specs

    def _video(self, params: dict) -> str:
        """The chosen clip model, refused here when no spec knows it — before anything runs."""
        chosen = str(params.get("video") or "").strip()
        if not chosen:
            return ""
        if "/" not in chosen:
            raise ValueError(f"video must be provider/model, not '{chosen}'")
        provider, model = chosen.split("/", 1)
        self._specs.spec(provider, model, "video")  # raises, naming the known models
        return chosen

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            backends = GenerationBackendResolver(self.config)
            generation = GenerationBackends(
                image=backends.resolve("keyframe_generate", "", ""),
                video=backends.resolve("shot_animate", "", ""),
                audio=bool(tool_config(self.config, PLUGIN, "shot_animate", "audio", False)),
            )

            def reasoner(tool: str, kind: str) -> ModelAccessReasoner:
                model = resolve_tool_model(self.config, PLUGIN, tool, kind=kind) or brain_model(self.config)
                return ModelAccessReasoner(self.models, model, self._images, timeout_s=150)

            service = self._run_factory(
                reasoner("product_analyze", "vision"),
                reasoner("campaign_brief", "text"),
                reasoner("media_check", "vision"),
                ToolProgressReporter(asyncio.get_running_loop(), on_update),
            )
            campaign = str(params.get("campaign") or "").strip()
            if campaign:
                report = await asyncio.to_thread(
                    service.resume,
                    campaign,
                    {str(k): str(v) for k, v in (params.get("picks") or {}).items()},
                    {str(k): str(v) for k, v in (params.get("redo") or {}).items()},
                    float(params.get("budget_usd") or 0),
                    generation,
                    self._video(params),
                )
            else:
                name = str(params.get("name") or "").strip()
                photos = [str(p) for p in params.get("photos") or []]
                if not name or not photos:
                    raise ValueError("start with `name` and `photos`, or continue with `campaign`")
                report = await asyncio.to_thread(
                    service.start,
                    name,
                    photos,
                    str(params.get("cast") or ""),
                    CreativeDirectionParams.parse(params),
                    str(params.get("recipe") or ""),
                    generation,
                    RunOptions(
                        resolution=str(params.get("resolution") or ""),
                        shots=tuple(str(s) for s in params["shots"]) if "shots" in params else None,
                        # Present-but-empty is meaningful here: [] means stills only.
                        animate=tuple(str(s) for s in params["animate"]) if "animate" in params else None,
                        variants=int(params.get("variants") or 0),
                        budget_usd=float(params.get("budget_usd") or 0),
                        gates=tuple(str(g) for g in params["gates"]) if "gates" in params else None,
                    ),
                    str(getattr(current_run_context(), "session_key", "") or ""),
                    self._video(params),
                )
        except PermissionError as e:
            # Refused at a gate: the refusal says what to do, and the gate's results come with
            # it, so a conversation that did not make them can still show them and ask.
            status = self._result(service.status(campaign))
            return ToolResult.text(
                f"campaign_run refused: {e}\n\nWhat the gate holds:\n{status.content[0].text}",
                details=status.details,
                artifacts=status.artifacts,
                is_error=True,
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"campaign_run: {type(e).__name__}: {e}", is_error=True)
        return self._result(report)

    @staticmethod
    def _result(report: CampaignReport) -> ToolResult:
        brief = report.brief
        cast = next((s.cast[0] for s in brief.shots if s.cast), "")
        lines = [
            f"campaign {report.campaign_id} · recipe {report.recipe_key} · format {brief.format_key}"
            + (f" · cast {cast}" if cast else "")
            + f" · waits at: {report.gate}",
        ]
        artifacts: list[str] = []
        if report.sheet:
            lines.append(f"shoot sheet: {report.sheet} ({report.sheet_score}/10)")
            if report.gate == SHEET:
                artifacts.append(report.sheet)
        elif report.gate == SHEET:
            lines.append("shoot sheet: FAILED its identity check (see the verdict); redo it with a change")
        if report.gate == BRIEF:
            lines.append(f"concept: {brief.concept}")
            lines.append(f"hook: {brief.hook}")
            lines.append("look: " + "; ".join(f"{k}: {v}" for k, v in brief.look.items()))
            for shot in (s for s in brief.shots if s.id in report.planned):
                lines.append(f"{shot.id} ({shot.purpose}): {shot.keyframe_prompt}")
                lines.append(f"   motion: {shot.motion_prompt}")
        for s in report.shots:
            line = f"{s.shot_id}: {s.status}"
            if s.still:
                line += f" · chosen still {s.still} ({s.still_score}/10)"
                artifacts.append(s.still)
            if report.gate == STILLS and s.alternatives:
                line += " · also passed: " + ", ".join(f"{p} ({s.stills[p]}/10)" for p in s.alternatives)
                artifacts += list(s.alternatives)
            if s.clip:
                line += f" · clip {s.clip} ({s.clip_check})"
                artifacts.append(s.clip)
            lines.append(line)
            lines += [f"   - {p}" for p in s.problems]
        lines.append(f"spent ${report.spent_usd:.2f}" + (f" · stopped: {report.stopped}" if report.stopped else ""))
        lines.append("NEXT: " + _NEXT[report.gate])
        return ToolResult.text("\n".join(lines), details={"report": report.to_dict()}, artifacts=artifacts)
