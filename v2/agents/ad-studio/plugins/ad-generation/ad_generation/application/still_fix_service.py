"""One still, one change the user asked for -> an edited still, checked like any other.

The user picks the still and says what is wrong ("fix the COACH lettering", "remove the extra
ring on the strap"); an image-editing model (Gemini — Nano Banana Pro — by default) changes only
that. The product photos ride along so the fix matches the real product, and the cast sheet so
the face stays hers. The result is the shot's next take, checked, and — when it passes and the
campaign is at a gate — joins the shot's passing stills, so the user can pick it there. It is
never chosen for them.
"""

from __future__ import annotations

from dataclasses import replace

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.application.quality_check_service import QualityCheckService
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest
from ad_generation.domain.media_verdict import MediaVerdict


class StillFixService:
    def __init__(
        self,
        generators: GeneratorCatalog,
        store: CampaignStore,
        cast: CastLibrary,
        checks: QualityCheckService,
        instructions: str,
        max_product_photos: int,
    ) -> None:
        self._generators = generators
        self._store = store
        self._cast = cast
        self._checks = checks
        self._instructions = instructions
        self._max_product_photos = max_product_photos

    def fix(
        self, campaign_id: str, shot_id: str, still: str, change: str, provider: str, model: str
    ) -> tuple[GeneratedMedia, MediaVerdict]:
        if not change.strip():
            raise ValueError("say what to change in the still")
        profile = self._store.profile(campaign_id)
        brief = self._store.brief(campaign_id)
        shot = brief.shot(shot_id)

        references = [still]
        legend = ["image 1: the still to edit"]
        if shot.shows_product:
            for i, photo in enumerate(profile.photos[: self._max_product_photos], 1):
                references.append(photo)
                legend.append(f"image {len(references)}: the product, {profile.name} (photo {i})")
        for name in shot.cast:
            references.append(self._cast.get(name).sheet)
            legend.append(
                f"image {len(references)}: identity reference for {name} — the face must stay "
                "this person's; ignore the clothes and background shown in it"
            )
        prompt = self._instructions.format(change=change.strip()) + "\n\nImages, in order:\n" + "\n".join(legend)
        if shot.shows_product and profile.must_keep:
            prompt += (
                f"\n\nThe product must match its photos exactly: {profile.description}. "
                "These details must be exact: " + "; ".join(profile.must_keep) + "."
            )
        request = ImageRequest(
            model=model,
            prompt=prompt,
            references=tuple(references),
            aspect_ratio=brief.aspect_ratio,
            variants=1,
            out_stem=self._store.still_stem(campaign_id, shot_id),
        )
        fixed = self._generators.image(provider).generate(request)[0]
        self._store.record(campaign_id, fixed)
        verdict = self._checks.check(campaign_id, shot_id, fixed.path, "")
        if verdict.passed:
            self._offer_at_gate(campaign_id, shot_id, verdict)
        return fixed, verdict

    def _offer_at_gate(self, campaign_id: str, shot_id: str, verdict: MediaVerdict) -> None:
        """A passing fix joins the shot's passing stills, so `picks` can choose it at the stills
        gate. A campaign made with the single-step tools has no recipe progress — nothing to
        join."""
        try:
            progress = self._store.progress(campaign_id)
        except KeyError:
            return
        outcome = progress.outcomes.get(shot_id)
        if outcome is None:
            return
        progress.outcomes[shot_id] = replace(outcome, stills={**outcome.stills, verdict.path: verdict.score})
        self._store.save_progress(campaign_id, progress)
