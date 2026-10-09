"""Images on Higgsfield. One job makes one image, so `variants` is that many jobs."""

from __future__ import annotations

from image_generation.budget_exceeded import BudgetExceeded
from image_generation.aspect_ratio import nearest_ratio, value
from image_generation.generated_media import GeneratedMedia
from image_generation.image_request import ImageRequest
from image_generation.higgsfield_api_client import HiggsfieldApiClient
from image_generation.higgsfield_session import HiggsfieldSession
from image_generation.media_downloader import MediaDownloader
from image_generation.model_spec_book import ModelSpecBook

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
            allowed = spec.get("aspect_ratios") or []
            ratio = request.aspect_ratio
            if allowed and ratio not in allowed:  # the nearest one the model makes, same orientation
                ratio = nearest_ratio(value(ratio), 1.0, allowed)
            params[fields["aspect_ratio"]] = ratio
        job_type = spec.get("job_type") or request.model
        if request.max_usd:
            cost = self._client.quote(job_type, params) * self._session.usd_per_credit() * request.variants
            if cost > request.max_usd:
                raise BudgetExceeded(request.model, cost, request.max_usd)
        # ALL SUBMITTED FIRST, then waited on: the provider works on them side by side, so two
        # variants take about as long as one.
        jobs = [self._client.submit("image", job_type, params) for _ in range(request.variants)]
        out = []
        for n, (job_id, credits) in enumerate(jobs, 1):
            job = self._client.wait(job_id, job_type, self._timeout_s)
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
