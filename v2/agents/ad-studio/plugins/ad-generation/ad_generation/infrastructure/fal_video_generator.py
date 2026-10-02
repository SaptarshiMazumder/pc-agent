"""Image-to-video on fal. Field names per model come from model_specs/fal.json."""

from __future__ import annotations

from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.video_request import VideoRequest
from ad_generation.infrastructure.fal_queue_client import FalQueueClient
from ad_generation.infrastructure.image_data_uri_encoder import ImageDataUriEncoder
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.infrastructure.price_calculator import PriceCalculator

PROVIDER = "fal"


class FalVideoGenerator:
    def __init__(
        self,
        queue: FalQueueClient,
        specs: ModelSpecBook,
        encoder: ImageDataUriEncoder,
        downloader: MediaDownloader,
        prices: PriceCalculator,
        timeout_s: float,
    ) -> None:
        self._queue = queue
        self._specs = specs
        self._encoder = encoder
        self._downloader = downloader
        self._prices = prices
        self._timeout_s = timeout_s

    def generate(self, request: VideoRequest) -> GeneratedMedia:
        spec = self._specs.spec(PROVIDER, request.model, "video")
        fields = spec["fields"]
        payload = dict(spec.get("fixed") or {})
        payload[fields["prompt"]] = request.prompt
        payload[fields["first_frame"]] = self._encoder.encode(request.first_frame)
        if "resolution" in fields:
            payload[fields["resolution"]] = request.resolution
        payload[fields["duration"]] = (
            str(request.duration_s) if spec.get("duration_type") == "string" else request.duration_s
        )
        if "audio" in fields:
            payload[fields["audio"]] = request.audio

        result = self._queue.run(request.model, payload, self._timeout_s)
        video = result.get("video") or {}
        if not video.get("url"):
            raise RuntimeError(f"fal returned no video for {request.model}: {str(result)[:300]}")
        stem = request.out_path.rsplit(".", 1)[0]
        path = self._downloader.save(video["url"], stem, video.get("content_type", ""), ".mp4")
        cost = self._prices.video_seconds(spec, request.resolution, request.duration_s, request.audio)
        return GeneratedMedia(
            path=path,
            kind="video",
            provider=PROVIDER,
            model=request.model,
            cost_usd=cost,
            cost_basis="estimate",
            detail={"seed": result.get("seed")},
        )
