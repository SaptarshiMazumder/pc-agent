"""Provider name -> its image and video backends. Built once, in the composition root."""

from __future__ import annotations

from ad_generation.application.interfaces.image_generator import ImageGenerator
from ad_generation.application.interfaces.video_generator import VideoGenerator


class ProviderGeneratorCatalog:
    def __init__(self, images: dict[str, ImageGenerator], videos: dict[str, VideoGenerator]) -> None:
        self._images = images
        self._videos = videos

    def image(self, provider: str) -> ImageGenerator:
        if provider not in self._images:
            raise KeyError(f"no image provider '{provider}' (known: {', '.join(sorted(self._images))})")
        return self._images[provider]

    def video(self, provider: str) -> VideoGenerator:
        if provider not in self._videos:
            raise KeyError(f"no video provider '{provider}' (known: {', '.join(sorted(self._videos))})")
        return self._videos[provider]
