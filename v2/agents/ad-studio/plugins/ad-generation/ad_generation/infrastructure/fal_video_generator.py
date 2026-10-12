"""Image-to-video on fal. Field names per model come from model_specs/fal.json:

    "endpoint"                  the fal endpoint, when it differs from the spec's key
    fields.references           the model also takes reference images (the cast sheet, stills)
    "first_frame_in_references" the model has no first-frame field: the first frame leads the
                                reference images (Seedance 2.5 reference-to-video)
    fields.aspect_ratio         the ratio of those the model accepts nearest the first frame's
    "duration_type"             "string" | "int" (default) — how the model wants the length
"""

from __future__ import annotations

from PIL import Image

from ad_generation.application.interfaces.budget_exceeded import BudgetExceeded
from ad_generation.domain.aspect_ratio import nearest_ratio
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.video_request import VideoRequest
from ad_generation.infrastructure.fal_queue_client import FalQueueClient
from ad_generation.infrastructure.image_data_uri_encoder import ImageDataUriEncoder
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.infrastructure.price_calculator import PriceCalculator
from ad_generation.infrastructure.run_workspace import RunWorkspace

PROVIDER = "fal"


class FalVideoGenerator:
    def __init__(
        self,
        queue: FalQueueClient,
        specs: ModelSpecBook,
        encoder: ImageDataUriEncoder,
        downloader: MediaDownloader,
        prices: PriceCalculator,
        workspace: RunWorkspace,
        timeout_s: float,
    ) -> None:
        self._queue = queue
        self._specs = specs
        self._encoder = encoder
        self._downloader = downloader
        self._prices = prices
        self._ws = workspace
        self._timeout_s = timeout_s

    def generate(self, request: VideoRequest) -> GeneratedMedia:
        spec = self._specs.spec(PROVIDER, request.model, "video")
        fields = spec["fields"]
        endpoint = str(spec.get("endpoint") or request.model)
        payload = dict(spec.get("fixed") or {})
        payload[fields["prompt"]] = request.prompt
        limit = int(spec.get("max_references") or 0) or None
        if spec.get("first_frame_in_references"):
            payload[fields["references"]] = [self._encoder.encode(p) for p in [request.first_frame, *request.references][:limit]]
        else:
            payload[fields["first_frame"]] = self._encoder.encode(request.first_frame)
            if "references" in fields and request.references:
                payload[fields["references"]] = [self._encoder.encode(p) for p in request.references[:limit]]
        if "resolution" in fields:
            offered = spec.get("resolutions") or {}
            if offered and request.resolution not in offered:
                raise ValueError(f"{request.model} makes {', '.join(offered)}, not {request.resolution}")
            payload[fields["resolution"]] = request.resolution
        if "aspect_ratio" in fields:
            with Image.open(self._ws.path(request.first_frame)) as im:
                payload[fields["aspect_ratio"]] = nearest_ratio(im.width, im.height, spec.get("aspect_ratios") or [])
        payload[fields["duration"]] = (
            str(request.duration_s) if spec.get("duration_type") == "string" else request.duration_s
        )
        if "audio" in fields:
            payload[fields["audio"]] = request.audio

        cost = self._prices.video_seconds(spec, request.resolution, request.duration_s, request.audio)
        if request.max_usd and cost > request.max_usd:
            raise BudgetExceeded(request.model, cost, request.max_usd)
        result = self._queue.run(endpoint, payload, self._timeout_s)
        video = result.get("video") or {}
        if not video.get("url"):
            raise RuntimeError(f"fal returned no video for {endpoint}: {str(result)[:300]}")
        stem = request.out_path.rsplit(".", 1)[0]
        path = self._downloader.save(video["url"], stem, video.get("content_type", ""), ".mp4")
        return GeneratedMedia(
            path=path,
            kind="video",
            provider=PROVIDER,
            model=request.model,
            cost_usd=cost,
            cost_basis="estimate",
            detail={"seed": result.get("seed")},
        )
