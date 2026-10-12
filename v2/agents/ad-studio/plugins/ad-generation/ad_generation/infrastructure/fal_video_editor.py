"""Edit or extend a clip on fal. What a model can do, the endpoint that does it and the fields it
takes come from its spec's `edit` / `extend` block (model_specs/fal.json):

    "edit":   {"endpoint": "fal-ai/kling-video/o3/standard/video-to-video/edit",
               "fields": {"prompt": "prompt", "video": "video_url", "references": "image_urls"},
               "max_references": 4, "price": {"per_second": {"any": 0.126}}}
    "extend": {"endpoint": "...", "fixed": {"task": "extension"}, "video_list": true,
               "fields": {"prompt": "prompt", "video": "video_urls", "duration": "duration"}, ...}

An edit is priced by the source clip's length, an extension by the seconds it adds; the price is
checked against the campaign's budget before anything is sent.
"""

from __future__ import annotations

from ad_generation.application.interfaces.budget_exceeded import BudgetExceeded
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.video_edit_request import VideoEditRequest
from ad_generation.infrastructure.fal_queue_client import FalQueueClient
from ad_generation.infrastructure.image_data_uri_encoder import ImageDataUriEncoder
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.infrastructure.video_data_uri_encoder import VideoDataUriEncoder

PROVIDER = "fal"
_DEFAULT_EXTEND_S = 5


class FalVideoEditor:
    def __init__(
        self,
        queue: FalQueueClient,
        specs: ModelSpecBook,
        images: ImageDataUriEncoder,
        clips: VideoDataUriEncoder,
        downloader: MediaDownloader,
        timeout_s: float,
    ) -> None:
        self._queue = queue
        self._specs = specs
        self._images = images
        self._clips = clips
        self._downloader = downloader
        self._timeout_s = timeout_s

    def can(self, model: str, mode: str) -> bool:
        return isinstance(self._specs.spec(PROVIDER, model, "video").get(mode), dict)

    def run(self, request: VideoEditRequest) -> GeneratedMedia:
        spec = self._specs.spec(PROVIDER, request.model, "video")
        block = spec.get(request.mode)
        if not isinstance(block, dict):
            raise ValueError(f"{request.model} cannot {request.mode} a clip on fal (its spec has no '{request.mode}' block)")
        fields = block["fields"]
        endpoint = str(block.get("endpoint") or spec.get("endpoint") or request.model)
        payload = dict(block.get("fixed") or {})
        payload[fields["prompt"]] = request.prompt
        clip = self._clips.encode(request.source)
        payload[fields["video"]] = [clip] if block.get("video_list") else clip
        if "references" in fields and request.references:
            limit = int(block.get("max_references") or len(request.references))
            payload[fields["references"]] = [self._images.encode(r) for r in request.references[:limit]]
        added = request.seconds or _DEFAULT_EXTEND_S
        if "duration" in fields and request.mode == "extend":
            payload[fields["duration"]] = str(added) if block.get("duration_type") == "string" else added

        rate = float(block["price"]["per_second"]["any"])
        cost = rate * (request.source_seconds if request.mode == "edit" else added)
        if request.max_usd and cost > request.max_usd:
            raise BudgetExceeded(request.model, cost, request.max_usd)
        result = self._queue.run(endpoint, payload, self._timeout_s)
        video = result.get("video") or {}
        if not video.get("url"):
            raise RuntimeError(f"fal returned no video for {endpoint}: {str(result)[:300]}")
        path = self._downloader.save(video["url"], request.out_path.rsplit(".", 1)[0], video.get("content_type", ""), ".mp4")
        return GeneratedMedia(
            path=path,
            kind="video",
            provider=PROVIDER,
            model=request.model,
            cost_usd=cost,
            cost_basis="estimate",
            detail={"mode": request.mode, "source": request.source, "endpoint": endpoint},
        )
