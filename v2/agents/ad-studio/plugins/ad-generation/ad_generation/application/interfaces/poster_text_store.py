"""Where a text ad's editable layers live: beside each design, with its clean picture."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.poster_text import PosterText


class PosterTextStore(Protocol):
    def keep_base(self, design: str) -> str:
        """Move a freshly made picture aside as the clean base of `design` -> the base's path."""
        ...

    def save(self, design: str, text: PosterText) -> None: ...

    def load(self, design: str) -> PosterText | None:
        """The design's layers; None for a design with its text drawn by the model."""
        ...

    def edit_path(self, design: str) -> str:
        """Where a not-yet-saved edit of `design` is drawn for the editor's preview."""
        ...
