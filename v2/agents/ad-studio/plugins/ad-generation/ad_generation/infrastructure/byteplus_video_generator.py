"""Image-to-video on BytePlus (Seedance), billed on the tokens the task reports.

`ratio` is `adaptive` because a first-frame task must be: the clip takes the still's shape, so
a 9:16 still makes a 9:16 clip. The last frame comes back too — it is what a clip is checked by.
"""

from __future__ import annotations

from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.video_request import VideoRequest
from ad_generation.infrastructure.byteplus_ark_client import BytePlusArkClient
from ad_generation.infrastructure.image_data_uri_encoder import ImageDataUriEncoder
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.infrastructure.price_calculator import PriceCalculator

PROVIDER = "byteplus"


class BytePlusVideoGenerator:
    def __init__(
        self,
        client: BytePlusArkClient,
        specs: ModelSpecBook,
        encoder: ImageDataUriEncoder,
        downloader: MediaDownloader,
        prices: PriceCalculator,
        timeout_s: float,
    ) -> None:
        self._client = client
        self._specs = specs
        self._encoder = encoder
        self._downloader = downloader
        self._prices = prices
        self._timeout_s = timeout_s

    def generate(self, request: VideoRequest) -> GeneratedMedia:
        spec = self._specs.spec(PROVIDER, request.model, "video")
        body = {
            "model": request.model,
            "content": [
                {"type": "text", "text": request.prompt},
                {"type": "image_url", "image_url": {"url": self._encoder.encode(request.first_frame)}, "role": "first_frame"},
            ],
            "resolution": request.resolution,
            "ratio": "adaptive",
            "duration": request.duration_s,
            "generate_audio": request.audio,
            "watermark": False,
            "return_last_frame": True,
        }
        task = self._client.run_video_task(body, self._timeout_s)
        content = task.get("content") or {}
        if not content.get("video_url"):
            raise RuntimeError(f"BytePlus task finished with no video: {str(task)[:300]}")
        stem = request.out_path.rsplit(".", 1)[0]
        path = self._downloader.save(content["video_url"], stem, "", ".mp4")
        last_frame = (
            self._downloader.save(content["last_frame_url"], stem + "-last", "", ".png")
            if content.get("last_frame_url")
            else ""
        )
        tokens = int((task.get("usage") or {}).get("completion_tokens") or 0)
        if not tokens:
            raise RuntimeError(f"BytePlus reported no token usage for task {task.get('id')}, so its cost is unknown")
        return GeneratedMedia(
            path=path,
            kind="video",
            provider=PROVIDER,
            model=request.model,
            cost_usd=self._prices.video_tokens(spec, request.resolution, tokens),
            cost_basis="exact",
            last_frame=last_frame,
            detail={"task": task.get("id"), "tokens": tokens, "seed": task.get("seed")},
        )
