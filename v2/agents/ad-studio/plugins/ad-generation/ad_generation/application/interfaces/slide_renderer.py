"""Turns a post's slides into the files Instagram takes."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.brand_profile import BrandProfile
from ad_generation.domain.post import Slide


class SlideRenderer(Protocol):
    def render(self, slide: Slide, brand: BrandProfile, fmt: str, out_stem: str, as_video: bool) -> str:
        """One plain slide (no design) at the format's size — the photo or clip filling it, its words
        over it -> the file's path:
        a .jpg for a still with fixed text, else an .mp4 (a clip, or a still whose text moves, or
        any slide when `as_video`)."""
        ...

    def stitch(self, parts: list[str], out: str) -> str:
        """Videos of the same size, one after another -> one video (a Reel)."""
        ...

    def still_of(self, clip: str, out: str) -> str:
        """One frame from the middle of a clip, as a .jpg at `out` -> its path."""
        ...

    def seconds(self, clip: str) -> float:
        """How long a clip runs."""
        ...
