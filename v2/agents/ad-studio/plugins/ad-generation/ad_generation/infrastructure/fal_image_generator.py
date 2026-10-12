"""Images on fal. The request's fields come from the model's spec (model_specs/fal.json):

    "endpoint"            the fal endpoint, when it differs from the spec's key (one endpoint
                          offered twice — Nano Banana Pro at 2K and at 4K)
    "without_references"  the endpoint to call when no reference image is given
    fields.first_reference
                          a model that EDITS a source image (Ideogram): the first reference is
                          sent as that image, the rest as its references
    fields.size / sizes   a size in pixels per aspect ratio, for models that take no ratio
    fields.count          absent when a model makes one image per call — then it is called once
                          per image wanted
"""

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
        endpoint = str(spec.get("endpoint") or request.model)
        payload = dict(spec.get("fixed") or {})
        payload[fields["prompt"]] = request.prompt
        refs = list(request.references)
        if spec.get("max_references"):
            refs = refs[: int(spec["max_references"])]
        if refs:
            if "first_reference" in fields:
                payload[fields["first_reference"]] = self._encoder.encode(refs[0])
                refs = refs[1:]
            if refs:
                payload[fields["references"]] = [self._encoder.encode(r) for r in refs]
        else:
            # The edit endpoints require an input image; the spec names the endpoint without one.
            endpoint = str(spec.get("without_references") or endpoint)
        if "aspect_ratio" in fields:
            payload[fields["aspect_ratio"]] = request.aspect_ratio
        if "size" in fields:
            sizes = spec["sizes"]
            if request.aspect_ratio not in sizes:
                raise ValueError(f"{request.model} has no size for {request.aspect_ratio} (has: {', '.join(sizes)})")
            payload[fields["size"]] = sizes[request.aspect_ratio]
        if request.variants > int(spec.get("max_count", 1)) and "count" in fields:
            raise ValueError(f"{request.model} makes at most {spec.get('max_count', 1)} images per call")

        each = self._prices.image(spec, len(request.references))
        if request.max_usd and each * request.variants > request.max_usd:
            raise BudgetExceeded(request.model, each * request.variants, request.max_usd)
        if "count" in fields:
            payload[fields["count"]] = request.variants
            calls = [payload]
        else:
            calls = [payload] * request.variants
        images = []
        for call in calls:
            result = self._queue.run(endpoint, call, self._timeout_s)
            images += result.get("images") or []
            if not result.get("images"):
                raise RuntimeError(f"fal returned no image for {endpoint}: {str(result)[:300]}")
        out = []
        for n, image in enumerate(images, 1):
            path = self._downloader.save(image["url"], f"{request.out_stem}-{n}", image.get("content_type", ""), ".png")
            out.append(
                GeneratedMedia(path=path, kind="image", provider=PROVIDER, model=request.model, cost_usd=each, cost_basis="estimate")
            )
        return out
