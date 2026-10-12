"""The places to shoot in."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.shoot_location import ShootLocation


class LocationLibrary(Protocol):
    def all(self) -> list[ShootLocation]: ...

    def get(self, key: str) -> ShootLocation:
        """Raises KeyError naming the locations there are."""
        ...
