"""Images on Higgsfield. One job makes one image, so `variants` is that many jobs."""

from __future__ import annotations

from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest
from ad_generation.infrastructure.higgsfield_api_client import HiggsfieldApiClient
from ad_generation.infrastructure.higgsfield_session import HiggsfieldSession
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook

PROVIDER = "higgsfield"


class HiggsfieldImageGenerator:
    def __init__(
        self,
        client: HiggsfieldApiClient,
        session: HiggsfieldSession,
        specs: ModelSpecBook,
        downloader: MediaDownloader,
        timeout_s: float,
    ) -> None:
        self._client = client
        self._session = session
        self._specs = specs
        self._downloader = downloader
        self._timeout_s = timeout_s

    def generate(self, request: ImageRequest) -> list[GeneratedMedia]:
        spec = self._specs.spec(PROVIDER, request.model, "image")
        fields = spec["fields"]
        params = dict(spec.get("fixed") or {})
        params[fields["prompt"]] = request.prompt
        if request.references:
            if len(request.references) > int(spec.get("max_references", 14)):
                raise ValueError(f"{request.model} takes at most {spec.get('max_references', 14)} reference images")
            params[fields["references"]] = [
                {"id": self._client.upload_image(r), "type": "media_input"} for r in request.references
            ]
        if "aspect_ratio" in fields:
            params[fields["aspect_ratio"]] = request.aspect_ratio
        out = []
        for n in range(1, request.variants + 1):
            job_id, credits = self._client.submit("image", spec.get("job_type") or request.model, params)
            job = self._client.wait(job_id, spec.get("job_type") or request.model, self._timeout_s)
            path = self._downloader.save(job["result_url"], f"{request.out_stem}-{n}", "", ".png")
            out.append(
                GeneratedMedia(
                    path=path,
                    kind="image",
                    provider=PROVIDER,
                    model=request.model,
                    cost_usd=credits * self._session.usd_per_credit(),
                    cost_basis="exact",
                    detail={"job_id": job_id, "credits": credits},
                )
            )
        return out
