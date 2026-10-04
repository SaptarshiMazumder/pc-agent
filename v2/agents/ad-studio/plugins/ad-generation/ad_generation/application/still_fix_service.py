"""One image, one change the user asked for -> an edited image, added to the step.

The user selects the image and says what is wrong ("fix the hands", "remove that ring"); an
image-editing model changes only that. The product photos ride along so the fix matches the real
product, and the cast sheet so the face stays the person's. The fix is scaled to the image's own
size, checked (advice), and added to the step's results — a fix of a fix is just another image.
It is never picked for the user.
"""

from __future__ import annotations

from dataclasses import replace

from ad_generation.application.campaign_checklist_loader import CampaignChecklistLoader
from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.application.interfaces.image_resizer import ImageResizer
from ad_generation.application.quality_check_service import QualityCheckService
from ad_generation.application.step_defaults import StepDefaults
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest
from ad_generation.domain.media_verdict import MediaVerdict


class StillFixService:
    def __init__(
        self,
        generators: GeneratorCatalog,
        store: CampaignStore,
        cast: CastLibrary,
        loader: CampaignChecklistLoader,
        defaults: StepDefaults,
        checks: QualityCheckService,
        instructions: str,
        max_product_photos: int,
        resizer: ImageResizer,
    ) -> None:
        self._generators = generators
        self._store = store
        self._cast = cast
        self._loader = loader
        self._defaults = defaults
        self._checks = checks
        self._instructions = instructions
        self._max_product_photos = max_product_photos
        self._resizer = resizer

    def fix(
        self, campaign_id: str, step_id: str, still: str, change: str, provider: str, model: str,
        text: tuple[str, ...] = (),
    ) -> tuple[GeneratedMedia, MediaVerdict]:
        """`text`: a text ad's words as they should read after the fix (its edited copy) — said in
        the prompt and read back by the check; left out, the brief's copy."""
        if not change.strip():
            raise ValueError("say what to change in the image")
        checklist = self._loader.load(campaign_id)
        step = checklist.step(step_id)
        profile = self._store.profile(campaign_id)
        cast = (checklist.cast_name,) if step.cast and checklist.cast_name else ()

        references = [still]
        legend = ["image 1: the image to edit"]
        if step.shows_product:
            for i, photo in enumerate(profile.photos[: self._max_product_photos], 1):
                references.append(photo)
                legend.append(f"image {len(references)}: the product, {profile.name} (photo {i})")
        for name in cast:
            references.append(self._defaults.face(name))
            legend.append(
                f"image {len(references)}: identity reference for {name} — the face must stay "
                "this person's; ignore the clothes and background shown in it"
            )
        prompt = self._instructions.format(change=change.strip()) + "\n\nImages, in order:\n" + "\n".join(legend)
        if step.shows_product and profile.must_keep:
            prompt += (
                f"\n\nThe product must match its photos exactly: {profile.description}. "
                "These details must be exact: " + "; ".join(profile.must_keep) + "."
            )
        if step.shows_product and profile.scale_and_label():
            prompt += "\n\n" + profile.scale_and_label()
        copy = tuple(text) or self._defaults.copy_lines(campaign_id, step)
        if copy:
            prompt += "\n\nEvery word of the text stays exactly as it is: " + " / ".join(f'"{c}"' for c in copy) + "."
        request = ImageRequest(
            model=model,
            prompt=prompt,
            references=tuple(references),
            aspect_ratio=self._store.brief(campaign_id).aspect_ratio,
            variants=1,
            out_stem=self._store.take_stem(campaign_id, step_id),
        )
        made = self._generators.image(provider).generate(request)[0]
        fixed = replace(made, step=step_id, detail={**made.detail, "fix_of": still, "change": change.strip()})
        # THE IMAGE'S OWN SIZE: a fix stands in for that image, so it must fit exactly where it did.
        self._resizer.match(fixed.path, still)
        self._store.record(campaign_id, fixed)
        verdict = self._checks.check(campaign_id, fixed.path, "", step.title, cast, step.shows_product, copy)
        return fixed, verdict
