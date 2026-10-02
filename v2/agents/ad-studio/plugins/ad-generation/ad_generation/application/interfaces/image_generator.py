"""The port every image backend implements. Services never know which one they hold."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest


class ImageGenerator(Protocol):
    def generate(self, request: ImageRequest) -> list[GeneratedMedia]:
        """Every variant, downloaded into the workspace. Raises on any failure — a refusal, a
        timeout or an expired link is an error the caller reports, never an empty list."""
        ...
