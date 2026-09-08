"""Port for turning an authorized image file into small browser-preview bytes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ImageThumbnailGenerationError(Exception):
    """The source is not an image the thumbnailer can safely render."""


class ImageThumbnailTooLargeError(ImageThumbnailGenerationError):
    """The source exceeds the thumbnailer's byte or decoded-pixel safety limit."""


@dataclass(frozen=True, slots=True)
class GeneratedImageThumbnail:
    data: bytes
    mime_type: str
    width: int
    height: int


class ImageThumbnailGenerator(Protocol):
    def generate(self, source: Path, *, max_edge: int) -> GeneratedImageThumbnail:
        """Render the first frame of ``source`` within a square ``max_edge`` bound."""

        ...
