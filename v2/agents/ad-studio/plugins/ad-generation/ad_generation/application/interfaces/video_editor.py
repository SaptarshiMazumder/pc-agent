"""A provider that can edit or extend an existing clip (the model's spec says which it can do)."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.video_edit_request import VideoEditRequest


class VideoEditor(Protocol):
    def can(self, model: str, mode: str) -> bool:
        """Whether `model` does `mode` ("edit" | "extend") natively."""
        ...

    def run(self, request: VideoEditRequest) -> GeneratedMedia: ...
