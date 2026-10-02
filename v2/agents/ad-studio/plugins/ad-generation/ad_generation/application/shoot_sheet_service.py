"""The SHOOT SHEET: the cast member as they will look in THIS ad — the brief's wardrobe, place and
light, from six angles — made once per campaign, before any still.

The identity sheet (cast_create) fixes who the person is, in neutral clothes. A still made from
it alone has to invent the outfit and the light each time, and a clip made from one still has
nothing to hold the face to when she turns. The shoot sheet is the bridge: every still and every
clip of the campaign references the same person in the same outfit under the same light.
"""

from __future__ import annotations

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.application.quality_check_service import QualityCheckService
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest
from ad_generation.domain.media_verdict import MediaVerdict
from ad_generation.domain.shot_checklist import LOOK_WITH_CAST


class ShootSheetService:
    def __init__(
        self,
        generators: GeneratorCatalog,
        store: CampaignStore,
        cast: CastLibrary,
        checks: QualityCheckService,
        instructions: str,
    ) -> None:
        self._generators = generators
        self._store = store
        self._cast = cast
        self._checks = checks
        self._instructions = instructions

    def make(
        self, campaign_id: str, cast_name: str, correction: str, provider: str, model: str
    ) -> tuple[GeneratedMedia, MediaVerdict]:
        member = self._cast.get(cast_name)
        look = self._store.brief(campaign_id).look
        missing = [k for k in LOOK_WITH_CAST if not str(look.get(k) or "").strip()]
        if missing:
            raise ValueError(f"the brief's look has no {', '.join(missing)}; the shoot sheet needs every look item")
        prompt = self._instructions.format(description=member.description, **{k: look[k] for k in LOOK_WITH_CAST})
        if correction:
            prompt += "\n\nChange from the last sheet: " + correction
        request = ImageRequest(
            model=model,
            prompt=prompt,
            references=(member.sheet,),
            aspect_ratio="16:9",
            variants=1,
            out_stem=self._store.sheet_stem(campaign_id),
        )
        sheet = self._generators.image(provider).generate(request)[0]
        self._store.record(campaign_id, sheet)
        verdict = self._checks.check_identity(campaign_id, sheet.path, cast_name)
        return sheet, verdict
