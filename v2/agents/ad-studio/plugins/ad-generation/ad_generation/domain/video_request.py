"""What a video generator is asked for — provider-neutral."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoRequest:
    model: str
    prompt: str
    first_frame: str  # workspace path of the still the clip starts from; its shape sets the clip's
    resolution: str  # "480p" | "720p" | "1080p"
    duration_s: int
    audio: bool
    out_path: str  # workspace path of the .mp4
    # Images of the same person for the model to hold the likeness to as she moves: the cast
    # sheet, the campaign's other stills, a shoot sheet. Used by models that take them.
    references: tuple[str, ...] = ()
