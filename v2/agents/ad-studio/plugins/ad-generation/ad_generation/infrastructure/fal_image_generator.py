"""Images on fal. The request's fields come from the model's spec (model_specs/fal.json)."""

from __future__ import annotations

from ad_generation.application.interfaces.budget_exceeded import BudgetExceeded
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest
from ad_generation.infrastructure.fal_queue_client import FalQueueClient
from ad_generation.infrastructure.image_data_uri_encoder import ImageDataUriEncoder
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.infrastructure.price_calculator import PriceCalculator

PROVIDER = "fal"


class FalImageGenerator:
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

    def generate(self, request: ImageRequest) -> list[GeneratedMedia]:
        spec = self._specs.spec(PROVIDER, request.model, "image")
        fields = spec["fields"]
        model = request.model
        payload = dict(spec.get("fixed") or {})
        payload[fields["prompt"]] = request.prompt
        if request.references:
            payload[fields["references"]] = [self._encoder.encode(r) for r in request.references]
        else:
            # The edit endpoints require an input image; the spec names the endpoint without one.
            model = spec.get("without_references") or model
        if "aspect_ratio" in fields:
            payload[fields["aspect_ratio"]] = request.aspect_ratio
        if "size" in fields:
            sizes = spec["sizes"]
            if request.aspect_ratio not in sizes:
                raise ValueError(f"{request.model} has no size for {request.aspect_ratio} (has: {', '.join(sizes)})")
            payload[fields["size"]] = sizes[request.aspect_ratio]
        if request.variants > int(spec.get("max_count", 1)):
            raise ValueError(f"{request.model} makes at most {spec.get('max_count', 1)} images per call")
        payload[fields["count"]] = request.variants

        each = self._prices.image(spec, len(request.references))
        if request.max_usd and each * request.variants > request.max_usd:
            raise BudgetExceeded(request.model, each * request.variants, request.max_usd)
        result = self._queue.run(model, payload, self._timeout_s)
        images = result.get("images") or []
        if not images:
            raise RuntimeError(f"fal returned no image for {model}: {str(result)[:300]}")
        out = []
        for n, image in enumerate(images, 1):
            path = self._downloader.save(image["url"], f"{request.out_stem}-{n}", image.get("content_type", ""), ".png")
            out.append(
                GeneratedMedia(path=path, kind="image", provider=PROVIDER, model=model, cost_usd=each, cost_basis="estimate")
            )
        return out
