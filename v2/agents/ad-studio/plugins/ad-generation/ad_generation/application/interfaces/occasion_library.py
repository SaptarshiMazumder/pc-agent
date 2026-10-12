"""The occasions sellers post for, with their dates."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.occasion import Occasion


class OccasionLibrary(Protocol):
    def all(self) -> list[Occasion]: ...

    def get(self, key: str) -> Occasion:
        """Raises KeyError naming the occasions there are."""
        ...
