"""Provider name -> its image, video and clip-editing backends. Built once, in the composition root."""

from __future__ import annotations

from ad_generation.application.interfaces.image_generator import ImageGenerator
from ad_generation.application.interfaces.video_editor import VideoEditor
from ad_generation.application.interfaces.video_generator import VideoGenerator


class ProviderGeneratorCatalog:
    def __init__(
        self,
        images: dict[str, ImageGenerator],
        videos: dict[str, VideoGenerator],
        editors: dict[str, VideoEditor],
    ) -> None:
        self._images = images
        self._videos = videos
        self._editors = editors

    def image(self, provider: str) -> ImageGenerator:
        if provider not in self._images:
            raise KeyError(f"no image provider '{provider}' (known: {', '.join(sorted(self._images))})")
        return self._images[provider]

    def video(self, provider: str) -> VideoGenerator:
        if provider not in self._videos:
            raise KeyError(f"no video provider '{provider}' (known: {', '.join(sorted(self._videos))})")
        return self._videos[provider]

    def has_editor(self, provider: str) -> bool:
        return provider in self._editors

    def editor(self, provider: str) -> VideoEditor:
        if provider not in self._editors:
            raise KeyError(f"no provider '{provider}' edits clips (known: {', '.join(sorted(self._editors)) or 'none'})")
        return self._editors[provider]
