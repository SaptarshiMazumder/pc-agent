"""A small copy of a result for the chat: what rides as an attachment (and so into every later model
request), while the full file stays in the step for the studio and the next generation."""

from __future__ import annotations

from typing import Protocol


class MediaPreviewer(Protocol):
    def preview(self, path: str) -> str:
        """A small JPEG of the image at `path`, saved beside it; its workspace path."""
        ...
