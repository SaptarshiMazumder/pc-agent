"""The port every video backend implements."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.video_request import VideoRequest


class VideoGenerator(Protocol):
    def generate(self, request: VideoRequest) -> GeneratedMedia:
        """The clip, downloaded into the workspace. Raises on any failure."""
        ...
