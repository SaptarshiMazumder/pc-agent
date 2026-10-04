"""Change a clip the user selected, or continue it — the result is added to the step, and the clip
it came from stays.

EDIT keeps the clip's motion, person and product and changes what the user said (light, place,
time of day…). It runs on a model that edits video natively (its spec has an `edit` block).

EXTEND continues the clip. A model that extends natively (Seedance 2.5) is sent the clip; any
other model makes a new clip whose FIRST frame is the source's LAST frame — so it starts exactly
where the source ends, with the action the user described next.

Every result is checked by its last frame (advice), and is never picked for the user.
"""

from __future__ import annotations

from dataclasses import replace

from ad_generation.application.campaign_checklist_loader import CampaignChecklistLoader
from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.clip_frames import ClipFrames
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.application.quality_check_service import QualityCheckService
from ad_generation.application.step_defaults import StepDefaults
from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.media_verdict import MediaVerdict
from ad_generation.domain.video_edit_request import VideoEditRequest
from ad_generation.domain.video_request import VideoRequest


class ClipEditService:
    def __init__(
        self,
        generators: GeneratorCatalog,
        store: CampaignStore,
        loader: CampaignChecklistLoader,
        defaults: StepDefaults,
        frames: ClipFrames,
        checks: QualityCheckService,
    ) -> None:
        self._generators = generators
        self._store = store
        self._loader = loader
        self._defaults = defaults
        self._frames = frames
        self._checks = checks

    def edit(
        self, campaign_id: str, step_id: str, clip: str, change: str, provider: str, model: str, max_usd: float = 0.0
    ) -> tuple[GeneratedMedia, MediaVerdict]:
        if not change.strip():
            raise ValueError("say what to change in the clip")
        editor = self._generators.editor(provider)
        if not editor.can(model, "edit"):
            raise ValueError(f"{provider}/{model} does not edit clips; generation_models lists the ones that do")
        checklist = self._loader.load(campaign_id)
        step = checklist.step(step_id)
        profile = self._store.profile(campaign_id)
        prompt = (
            f"Edit this video: {change.strip()}. Keep everything else as it is — the person, their face and "
            f"outfit, the motion, the framing and the camera move. The product ({profile.name}) stays exactly as it "
            "is: " + "; ".join(profile.must_keep) + ". " + profile.scale_and_label()
            + " No cuts, no new people, no added captions or titles."
        )
        made = editor.run(
            VideoEditRequest(
                model=model,
                mode="edit",
                prompt=prompt,
                source=clip,
                out_path=self._store.take_stem(campaign_id, step_id) + ".mp4",
                source_seconds=self._frames.seconds(clip),
                references=self._defaults.references(campaign_id, checklist, step),
                max_usd=max_usd,
            )
        )
        return self._land(campaign_id, checklist, step, made, clip)

    def extend(
        self,
        campaign_id: str,
        step_id: str,
        clip: str,
        what_next: str,
        seconds: int,
        provider: str,
        model: str,
        max_usd: float = 0.0,
    ) -> tuple[GeneratedMedia, MediaVerdict]:
        if not what_next.strip():
            raise ValueError("say what happens next in the clip")
        checklist = self._loader.load(campaign_id)
        step = checklist.step(step_id)
        profile = self._store.profile(campaign_id)
        prompt = (
            f"Continue this shot: {what_next.strip()}. The same person in the same outfit, the same place and "
            f"light. The product ({profile.name}) stays exactly as it is: " + "; ".join(profile.must_keep)
            + ". " + profile.scale_and_label()
            + " Smooth, natural motion; no cuts, no new people, no added captions or titles."
        )
        out = self._store.take_stem(campaign_id, step_id) + ".mp4"
        references = self._defaults.references(campaign_id, checklist, step)
        editor = self._generators.editor(provider) if self._generators.has_editor(provider) else None
        if editor and editor.can(model, "extend"):
            made = editor.run(
                VideoEditRequest(
                    model=model,
                    mode="extend",
                    prompt=prompt,
                    source=clip,
                    out_path=out,
                    source_seconds=self._frames.seconds(clip),
                    seconds=seconds,
                    references=references,
                    max_usd=max_usd,
                )
            )
        else:
            # Any model: a new clip that starts on the source's last frame.
            made = self._generators.video(provider).generate(
                VideoRequest(
                    model=model,
                    prompt=prompt,
                    first_frame=self._frames.last_frame(clip),
                    resolution=step.resolution or "720p",
                    duration_s=seconds or 5,
                    audio=False,
                    out_path=out,
                    references=references,
                    max_usd=max_usd,
                )
            )
        return self._land(campaign_id, checklist, step, made, clip)

    def _land(
        self, campaign_id: str, checklist: CampaignChecklist, step: CampaignStep, made: GeneratedMedia, source: str
    ) -> tuple[GeneratedMedia, MediaVerdict]:
        """Record it in the step, then check it by its last frame."""
        made = replace(made, step=step.id, detail={**made.detail, "from_clip": source})
        self._store.record(campaign_id, made)
        last = made.last_frame or self._frames.last_frame(made.path)
        cast = (checklist.cast_name,) if step.cast and checklist.cast_name else ()
        verdict = self._checks.check(campaign_id, made.path, last, step.title, cast, step.shows_product)
        return made, verdict
