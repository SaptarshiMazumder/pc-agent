"""A chosen still -> the shot's clip, the still as its first frame.

With it go images of the same person for the model to hold the likeness to as she moves: the
cast sheet, and whatever the caller adds — the campaign's other stills, the shoot sheet. A model
that takes no references (its spec names no field) ignores them."""

from __future__ import annotations

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.video_request import VideoRequest


class AnimationService:
    def __init__(self, generators: GeneratorCatalog, store: CampaignStore, cast: CastLibrary) -> None:
        self._generators = generators
        self._store = store
        self._cast = cast

    def animate(
        self,
        campaign_id: str,
        shot_id: str,
        still: str,
        resolution: str,
        audio: bool,
        correction: str,
        provider: str,
        model: str,
        companions: tuple[str, ...] = (),
        max_usd: float = 0.0,
    ) -> GeneratedMedia:
        profile = self._store.profile(campaign_id)
        shot = self._store.brief(campaign_id).shot(shot_id)

        # The composed motion prompt already holds the look (outfit, place, light, weather); the
        # product's exact details are added here, where the profile is.
        prompt = shot.motion_prompt
        if shot.shows_product:
            prompt += (
                f"\n\nKeep the product ({profile.name}) exactly as in the first frame — "
                + "; ".join(profile.must_keep) + "."
            )
        if correction:
            prompt += "\n\nFix from the last attempt: " + correction

        request = VideoRequest(
            model=model,
            prompt=prompt,
            first_frame=still,
            resolution=resolution,
            duration_s=shot.duration_s,
            audio=audio,
            out_path=self._store.clip_path(campaign_id, shot_id),
            references=tuple(dict.fromkeys([*companions, *(self._cast.get(n).sheet for n in shot.cast)])),
            max_usd=max_usd,
        )
        clip = self._generators.video(provider).generate(request)
        self._store.record(campaign_id, clip)
        return clip
