"""Make one image the exact pixel size of another — a fix lands at the still's own size."""

from __future__ import annotations

from typing import Protocol


class ImageResizer(Protocol):
    def match(self, path: str, like: str) -> None:
        """Resize `path` in place to the width and height of `like` (workspace paths)."""
        ...
