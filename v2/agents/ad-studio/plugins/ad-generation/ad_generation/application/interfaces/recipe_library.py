"""The recipes — one per kind of product."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.recipe import Recipe


class RecipeLibrary(Protocol):
    def all(self) -> list[Recipe]: ...

    def get(self, key: str) -> Recipe:
        """Raises KeyError naming the recipes there are."""
        ...
