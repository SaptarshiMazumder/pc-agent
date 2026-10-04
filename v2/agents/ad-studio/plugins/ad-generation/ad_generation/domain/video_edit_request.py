"""What a video editor is asked for: change an existing clip, or continue it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoEditRequest:
    model: str
    mode: str  # "edit" (same clip, changed) | "extend" (the clip continued)
    prompt: str
    source: str  # workspace path of the clip being edited or extended
    out_path: str  # workspace path of the new .mp4
    source_seconds: float  # how long the source runs — some providers price an edit by it
    seconds: int = 0  # extend: how many seconds to add; 0 = the model's default
    direction: str = "forward"  # extend: forward (after the clip) | backward (before it)
    references: tuple[str, ...] = ()  # images that hold the person and the product
    audio: bool = False
    max_usd: float = 0.0  # what the campaign has left; 0 = no cap
