"""The PRODUCT SHEET: the product alone, six views in a fixed grid, made from the user's photos —
the product's counterpart of a cast member's sheet.

The product photos rarely show every side; a shot that turns the product (or a clip that moves
it) then invents the back. The sheet settles every side once, where the user can look at it and
redo it, and its panels — cut apart by position — ride with every later image and clip as
separate references. It invents what the photos do not show, which is why the user approves it;
real angle photos uploaded into the step replace it.
"""

from __future__ import annotations

from dataclasses import replace

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.application.quality_check_service import QualityCheckService
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest
from ad_generation.domain.media_verdict import MediaVerdict
from ad_generation.domain.product_sheet_layout import ASPECT


class ProductSheetService:
    def __init__(
        self,
        generators: GeneratorCatalog,
        store: CampaignStore,
        checks: QualityCheckService,
        instructions: str,
        max_product_photos: int,
    ) -> None:
        self._generators = generators
        self._store = store
        self._checks = checks
        self._instructions = instructions
        self._max_product_photos = max_product_photos

    def make(
        self, campaign_id: str, step_id: str, change: str, provider: str, model: str, max_usd: float = 0.0
    ) -> tuple[GeneratedMedia, MediaVerdict]:
        profile = self._store.profile(campaign_id)
        photos = list(profile.photos[: self._max_product_photos])
        if not photos:
            raise ValueError("a product sheet is made from the product's photos, and this campaign has none")
        prompt = self._instructions.format(
            name=profile.name, description=profile.description, must_keep="; ".join(profile.must_keep) or "every detail"
        )
        if profile.scale_and_label():
            prompt += "\n\n" + profile.scale_and_label()
        prompt += "\n\nImages, in order:\n" + "\n".join(
            f"image {n}: the product, {profile.name} (photo {n})" for n in range(1, len(photos) + 1)
        )
        if change.strip():
            prompt += "\n\nChange from the last sheet: " + change.strip()
        request = ImageRequest(
            model=model,
            prompt=prompt,
            references=tuple(photos),
            aspect_ratio=ASPECT,
            variants=1,
            out_stem=self._store.take_stem(campaign_id, step_id),
            max_usd=max_usd,
        )
        sheet = replace(self._generators.image(provider).generate(request)[0], step=step_id)
        self._store.record(campaign_id, sheet)
        verdict = self._checks.check(campaign_id, sheet.path, "", "product reference sheet", (), True)
        return sheet, verdict
