"""The account's brand profile."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.brand_profile import BrandProfile


class BrandStore(Protocol):
    def get(self) -> BrandProfile:
        """The saved profile; the defaults when none is saved yet."""
        ...

    def save(self, brand: BrandProfile) -> None: ...
