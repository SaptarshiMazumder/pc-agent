"""A video step -> a clip: a first frame, a prompt and references, any number of times.

The first frame is the source step's pick unless the run names another image; the prompt is the
step's default (its brief scene's motion) unless the run gives its own. References hold the
person's likeness as she moves (the shoot sheet, the cast sheet); a model that takes none ignores
them. The first frame is kept with the clip, so a clip made from an image the user has since
replaced can be shown as made from an older one.
"""

from __future__ import annotations

from dataclasses import replace

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.application.step_defaults import StepDefaults
from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.step_run import StepRun
from ad_generation.domain.video_request import VideoRequest


class AnimationService:
    def __init__(self, generators: GeneratorCatalog, store: CampaignStore, defaults: StepDefaults) -> None:
        self._generators = generators
        self._store = store
        self._defaults = defaults

    def animate(
        self,
        campaign_id: str,
        checklist: CampaignChecklist,
        step: CampaignStep,
        run: StepRun,
        first_frame: str,
        seconds: int,
        resolution: str,
        audio: bool,
        provider: str,
        model: str,
        max_usd: float = 0.0,
    ) -> GeneratedMedia:
        prompt = run.prompt or self._defaults.prompt(campaign_id, step)
        if not prompt:
            raise ValueError(f"step {step.id} has no prompt of its own and no brief scene — give one")
        profile = self._store.profile(campaign_id)
        if step.shows_product:
            prompt += (
                f"\n\nKeep the product ({profile.name}) exactly as in the first frame — "
                + "; ".join(profile.must_keep) + "."
            )
            if profile.scale_and_label():
                prompt += " " + profile.scale_and_label() + " The label stays readable as it moves."
        if run.change:
            prompt += "\n\nChange from the last clip: " + run.change
        references = (
            run.references if run.references is not None else self._defaults.references(campaign_id, checklist, step)
        )
        request = VideoRequest(
            model=model,
            prompt=prompt,
            first_frame=first_frame,
            resolution=resolution,
            duration_s=seconds,
            audio=audio,
            out_path=self._store.take_stem(campaign_id, step.id) + ".mp4",
            references=tuple(references),
            max_usd=max_usd,
        )
        clip = self._generators.video(provider).generate(request)
        clip = replace(clip, step=step.id, detail={**clip.detail, "first_frame": first_frame})
        self._store.record(campaign_id, clip)
        return clip
