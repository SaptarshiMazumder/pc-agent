"""Images on BytePlus (Seedream). One image per call, so variants are separate calls."""

from __future__ import annotations

from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest
from ad_generation.infrastructure.byteplus_ark_client import BytePlusArkClient
from ad_generation.infrastructure.image_data_uri_encoder import ImageDataUriEncoder
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.infrastructure.price_calculator import PriceCalculator

PROVIDER = "byteplus"


class BytePlusImageGenerator:
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

    def generate(self, request: ImageRequest) -> list[GeneratedMedia]:
        spec = self._specs.spec(PROVIDER, request.model, "image")
        sizes = spec["sizes"]
        if request.aspect_ratio not in sizes:
            raise ValueError(f"{request.model} has no size for {request.aspect_ratio} (has: {', '.join(sizes)})")
        body = {
            "model": request.model,
            "prompt": request.prompt,
            "size": sizes[request.aspect_ratio],
            "response_format": "url",
            # BytePlus stamps a watermark unless told not to.
            "watermark": False,
        }
        if request.references:
            uris = [self._encoder.encode(r) for r in request.references]
            body["image"] = uris[0] if len(uris) == 1 else uris
        each = self._prices.image(spec, len(request.references))

        out = []
        for n in range(1, request.variants + 1):
            result = self._client.generate_image(body, self._timeout_s)
            data = result.get("data") or []
            if not data or not data[0].get("url"):
                raise RuntimeError(f"BytePlus returned no image for {request.model}: {str(result)[:300]}")
            path = self._downloader.save(data[0]["url"], f"{request.out_stem}-{n}", "", ".jpg")
            out.append(
                GeneratedMedia(path=path, kind="image", provider=PROVIDER, model=request.model, cost_usd=each, cost_basis="estimate")
            )
        return out
