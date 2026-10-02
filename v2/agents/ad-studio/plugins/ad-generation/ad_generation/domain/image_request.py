"""What an image generator is asked for — provider-neutral."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageRequest:
    model: str
    prompt: str
    # Workspace paths, IN THE ORDER the prompt refers to them ("image 1", "image 2", ...).
    references: tuple[str, ...]
    aspect_ratio: str  # "9:16", "1:1", ...
    variants: int
    out_stem: str  # workspace path without extension; variant n is saved as <stem>-<n>.<ext>
    # What the campaign has left; 0 = no cap (see VideoRequest.max_usd).
    max_usd: float = 0.0
