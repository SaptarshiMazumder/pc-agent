"""Edit or extend a clip on Higgsfield. What a model can do, and the fields it takes for it, come
from its spec's `edit` / `extend` block (model_specs/higgsfield.json):

    "edit":   {"fixed": {"mode": "video_edit"}, "fields": {"prompt": …, "video": "video_references", …}}
    "extend": {"fixed": {"mode": "video_extension"}, "fields": {…, "direction": "extension_mode"}}

The source clip is uploaded (the same signed-slot flow as an image) and attached as a video
reference; the price is quoted first and refused past the campaign's budget.
"""

from __future__ import annotations

from ad_generation.application.interfaces.budget_exceeded import BudgetExceeded
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.video_edit_request import VideoEditRequest
from ad_generation.infrastructure.higgsfield_api_client import HiggsfieldApiClient
from ad_generation.infrastructure.higgsfield_session import HiggsfieldSession
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook

PROVIDER = "higgsfield"


class HiggsfieldVideoEditor:
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

    def can(self, model: str, mode: str) -> bool:
        return isinstance(self._specs.spec(PROVIDER, model, "video").get(mode), dict)

    def run(self, request: VideoEditRequest) -> GeneratedMedia:
        spec = self._specs.spec(PROVIDER, request.model, "video")
        block = spec.get(request.mode)
        if not isinstance(block, dict):
            raise ValueError(f"{request.model} cannot {request.mode} a clip (its spec has no '{request.mode}' block)")
        fields = block["fields"]
        job_type = block.get("job_type") or spec.get("job_type") or request.model
        params = dict(block.get("fixed") or {})
        params[fields["prompt"]] = request.prompt
        params[fields["video"]] = [{"id": self._client.upload_video(request.source), "type": "media_input"}]
        if "references" in fields and request.references:
            limit = int(block.get("max_references") or len(request.references))
            params[fields["references"]] = [
                {"id": self._client.upload_image(r), "type": "media_input"} for r in request.references[:limit]
            ]
        if "duration" in fields and request.seconds:
            params[fields["duration"]] = request.seconds
        if "direction" in fields:
            params[fields["direction"]] = request.direction
        if "audio" in fields:
            values = spec.get("audio_values") or {"true": True, "false": False}
            params[fields["audio"]] = values["true" if request.audio else "false"]
        # Some edits are priced by the source clip's length, which they are not sent as a field.
        quote_params = dict(params)
        if block.get("quote_duration"):
            quote_params["duration"] = max(1, round(request.source_seconds))
        if request.max_usd:
            cost = self._client.quote(job_type, quote_params) * self._session.usd_per_credit()
            if cost > request.max_usd:
                raise BudgetExceeded(request.model, cost, request.max_usd)
        job_id, credits = self._client.submit("video", job_type, params)
        job = self._client.wait(job_id, job_type, self._timeout_s)
        path = self._downloader.save(job["result_url"], request.out_path.rsplit(".", 1)[0], "", ".mp4")
        return GeneratedMedia(
            path=path,
            kind="video",
            provider=PROVIDER,
            model=request.model,
            cost_usd=credits * self._session.usd_per_credit(),
            cost_basis="exact",
            detail={"job_id": job_id, "credits": credits, "mode": request.mode, "source": request.source},
        )
