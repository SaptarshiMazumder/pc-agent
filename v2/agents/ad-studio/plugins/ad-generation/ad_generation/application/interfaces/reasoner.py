"""The language model the reading, briefing and checking steps think with — the agent's own
model access, behind a port so the services never touch it directly."""

from __future__ import annotations

from typing import Protocol


class Reasoner(Protocol):
    def read_images(self, prompt: str, images: list[str]) -> dict:
        """One prompt + workspace images -> the model's JSON answer. Raises ValueError when the
        answer is not JSON."""
        ...

    def think(self, prompt: str) -> dict:
        """One text prompt -> the model's JSON answer. Raises ValueError when it is not JSON."""
        ...
