"""Renders a slide's design (HTML/CSS) into the files Instagram takes.

A design is a page at the format's exact size. Pictures are workspace paths. A clip plays in the
one element marked `data-video="<clip path>"`; elements marked `data-over` are drawn ABOVE the
clip, everything else below it. Motion is CSS animation only (no scripts)."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.post import ClipEdit


class DesignRenderer(Protocol):
    def still(self, html: str, fmt: str, out: str) -> str:
        """The design as one picture (a clip's slot shows its middle frame) -> the file's path."""
        ...

    def motion(self, html: str, fmt: str, out: str, seconds: float, clip: str = "", edit: ClipEdit | None = None) -> str:
        """The design as an .mp4: its CSS animations played over `seconds` — or, with a clip, over
        the clip's length after its cut, the clip playing in its slot -> the file's path."""
        ...

    def animated(self, html: str) -> bool:
        """Whether the design moves (CSS animations)."""
        ...
