"""Which backend serves a provider name — so a provider is picked at run time, by config or per
call, and a new one is an adapter plus a catalog entry, never a change to a service."""

from __future__ import annotations

from typing import Protocol

from ad_generation.application.interfaces.image_generator import ImageGenerator
from ad_generation.application.interfaces.video_editor import VideoEditor
from ad_generation.application.interfaces.video_generator import VideoGenerator


class GeneratorCatalog(Protocol):
    def image(self, provider: str) -> ImageGenerator:
        """Raises KeyError naming the known providers when `provider` has no image backend."""
        ...

    def video(self, provider: str) -> VideoGenerator:
        """Raises KeyError naming the known providers when `provider` has no video backend."""
        ...

    def has_editor(self, provider: str) -> bool:
        """Whether `provider` can edit or extend a clip it is sent."""
        ...

    def editor(self, provider: str) -> VideoEditor:
        """Raises KeyError naming the providers that edit clips when `provider` does not."""
        ...
