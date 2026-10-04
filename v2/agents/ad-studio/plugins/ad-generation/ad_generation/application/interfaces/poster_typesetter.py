"""Draws a text ad's layers onto its clean picture."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.poster_text import TextLayer


class PosterTypesetter(Protocol):
    def render(self, base: str, layers: tuple[TextLayer, ...], out: str) -> None:
        """Workspace paths: the clean picture in, the finished design out. A piece of text that
        cannot fit its area even at the smallest size raises, naming it."""
        ...
