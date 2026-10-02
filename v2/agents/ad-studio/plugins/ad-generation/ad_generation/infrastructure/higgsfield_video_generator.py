"""Image-to-video on Higgsfield. Field names and options per model come from model_specs/higgsfield.json.

A model that takes reference images (Seedance's omni_reference) gets the request's references —
the cast sheet and the campaign's other stills — uploaded and attached; a model whose spec names
no `references` field (Kling 3) animates from the first frame alone, and the references are
dropped, not sent to a field that does not exist.
"""

from __future__ import annotations

from ad_generation.application.interfaces.budget_exceeded import BudgetExceeded
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.video_request import VideoRequest
from ad_generation.infrastructure.higgsfield_api_client import HiggsfieldApiClient
from ad_generation.infrastructure.higgsfield_session import HiggsfieldSession
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook

PROVIDER = "higgsfield"


class HiggsfieldVideoGenerator:
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

    def generate(self, request: VideoRequest) -> GeneratedMedia:
        spec = self._specs.spec(PROVIDER, request.model, "video")
        fields = spec["fields"]
        params = dict(spec.get("fixed") or {})
        params[fields["prompt"]] = request.prompt
        params[fields["first_frame"]] = {"id": self._client.upload_image(request.first_frame), "type": "media_input"}
        if "references" in fields and request.references:
            params[fields["references"]] = [
                {"id": self._client.upload_image(r), "type": "media_input"} for r in request.references
            ]
        params[fields["duration"]] = request.duration_s
        if "resolution" in fields:
            params[fields["resolution"]] = request.resolution
        if "aspect_ratio" in fields:
            params[fields["aspect_ratio"]] = _aspect_of(request.first_frame)
        if "audio" in fields:
            values = spec.get("audio_values") or {"true": True, "false": False}
            params[fields["audio"]] = values["true" if request.audio else "false"]

        job_type = spec.get("job_type") or request.model
        if request.max_usd:
            cost = self._client.quote(job_type, params) * self._session.usd_per_credit()
            if cost > request.max_usd:
                raise BudgetExceeded(request.model, cost, request.max_usd)
        job_id, credits = self._client.submit("video", job_type, params)
        job = self._client.wait(job_id, spec.get("job_type") or request.model, self._timeout_s)
        stem = request.out_path.rsplit(".", 1)[0]
        path = self._downloader.save(job["result_url"], stem, "", ".mp4")
        return GeneratedMedia(
            path=path,
            kind="video",
            provider=PROVIDER,
            model=request.model,
            cost_usd=credits * self._session.usd_per_credit(),
            cost_basis="exact",
            detail={"job_id": job_id, "credits": credits},
        )


def _aspect_of(image_path: str) -> str:
    """The still's own ratio, as the models name them; the clip keeps the still's frame."""
    from PIL import Image

    from ad_generation.infrastructure.run_workspace import RunWorkspace

    with Image.open(RunWorkspace().path(image_path)) as im:
        w, h = im.size
    ratio = w / h
    named = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0, "3:4": 3 / 4, "4:3": 4 / 3}
    return min(named, key=lambda k: abs(named[k] - ratio))
