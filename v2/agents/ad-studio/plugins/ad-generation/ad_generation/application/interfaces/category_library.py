"""The product categories — one per kind of thing the shop sells."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.category_profile import CategoryProfile


class CategoryLibrary(Protocol):
    def all(self) -> list[CategoryProfile]: ...

    def get(self, key: str) -> CategoryProfile:
        """Raises KeyError naming the categories there are."""
        ...
