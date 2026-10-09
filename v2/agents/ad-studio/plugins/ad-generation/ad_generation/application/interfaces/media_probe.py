"""What a video file is: how long it runs, its frame rate, and whether it plays from end to end."""

from __future__ import annotations

from typing import Protocol


class MediaProbe(Protocol):
    def seconds(self, path: str) -> float:
        """How long it runs. Raises ValueError when that cannot be read."""
        ...

    def frame_rate(self, path: str) -> float:
        """Frames per second. Raises ValueError when that cannot be read."""
        ...

    def decode_errors(self, path: str) -> str:
        """Decode it from end to end -> what went wrong ('' when it plays cleanly)."""
        ...
