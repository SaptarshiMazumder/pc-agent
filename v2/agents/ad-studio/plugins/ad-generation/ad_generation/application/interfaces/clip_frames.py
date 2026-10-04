"""Frames out of a clip — what a clip is checked by, and what an extension starts from."""

from __future__ import annotations

from typing import Protocol


class ClipFrames(Protocol):
    def last_frame(self, clip: str) -> str:
        """The clip's last frame saved beside it (<clip stem>-last.png); its workspace path."""
        ...

    def seconds(self, clip: str) -> float:
        """How long the clip runs."""
        ...
