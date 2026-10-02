"""A shot of the brief -> its stills: the cast member and the product, in the shot's scene.

THE STILL LOCKS THE LOOK. Face, outfit, product and light are settled here, where a retry costs
cents; the clip only animates what the still already got right.
"""

from __future__ import annotations

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest


class KeyframeService:
    def __init__(
        self, generators: GeneratorCatalog, store: CampaignStore, cast: CastLibrary, max_product_photos: int
    ) -> None:
        self._generators = generators
        self._store = store
        self._cast = cast
        self._max_product_photos = max_product_photos

    def generate(
        self,
        campaign_id: str,
        shot_id: str,
        variants: int,
        correction: str,
        provider: str,
        model: str,
        shoot_sheet: str = "",
    ) -> list[GeneratedMedia]:
        profile = self._store.profile(campaign_id)
        brief = self._store.brief(campaign_id)
        shot = brief.shot(shot_id)

        references: list[str] = []
        legend: list[str] = []
        if shoot_sheet and shot.cast:
            # The campaign's own sheet: this person in THIS outfit and light. It leads; the
            # identity sheet below still anchors the face.
            references.append(shoot_sheet)
            legend.append(
                f"image 1: {shot.cast[0]} as she appears in this ad — match this outfit, hair styling "
                "and light exactly"
            )
        for name in shot.cast:
            member = self._cast.get(name)
            references.append(member.sheet)
            # IDENTITY ONLY. The sheet shows the person in some outfit, and image models copy what a
            # reference shows — so it is named as a face-and-body reference and its clothes are
            # explicitly ruled out; the outfit comes from the brief's look, stated in the prompt.
            legend.append(
                f"image {len(references)}: identity reference for {name} — use ONLY this person's "
                "face, hair, skin tone and build; ignore the clothes, jewellery, accessories and "
                "background shown in it"
            )
        if shot.shows_product:
            for i, photo in enumerate(profile.photos[: self._max_product_photos], 1):
                references.append(photo)
                legend.append(f"image {len(references)}: the product, {profile.name} (photo {i})")

        prompt = shot.keyframe_prompt
        if legend:
            prompt += "\n\nReference images, in order:\n" + "\n".join(legend)
        if shot.shows_product and profile.must_keep:
            prompt += (
                f"\n\nReproduce the product exactly as in its photos: {profile.description}. "
                "These details must be exact: " + "; ".join(profile.must_keep) + "."
            )
        if correction:
            prompt += "\n\nFix from the last attempt: " + correction

        request = ImageRequest(
            model=model,
            prompt=prompt,
            references=tuple(references),
            aspect_ratio=brief.aspect_ratio,
            variants=variants,
            out_stem=self._store.still_stem(campaign_id, shot_id),
        )
        stills = self._generators.image(provider).generate(request)
        for still in stills:
            self._store.record(campaign_id, still)
        return stills
