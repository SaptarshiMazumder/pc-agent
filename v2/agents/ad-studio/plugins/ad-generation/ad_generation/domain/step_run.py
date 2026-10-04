"""What one run of a step is asked for. Every field is optional: what is left empty comes from the
step's last run, then from the defaults (the brief's scene, the cast and product references, the
configured models)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StepRun:
    change: str = ""  # brief / sheet: what to change; images / video: added to the prompt
    prompt: str = ""  # images / video: the whole prompt, replacing the default
    references: tuple[str, ...] | None = None  # images / video: exactly these; None = the defaults
    count: int = 0  # images: how many
    like: str = ""  # images: an image to make more like (rides last, as the composition to vary)
    model: str = ""  # "provider/model"
    first_frame: str = ""  # video: the image it starts from; "" = the source step's pick
    seconds: int = 0  # video: length
    resolution: str = ""  # video: resolution
    # brief: direction fields the user changed (a headline, the location…) — each replaces the old
    direction: dict = field(default_factory=dict)
