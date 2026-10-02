"""Which provider and model make a run's stills and clips, and whether clips carry sound."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenerationBackends:
    image: tuple[str, str]  # (provider, model)
    video: tuple[str, str]  # (provider, model)
    audio: bool
