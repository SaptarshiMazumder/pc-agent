"""The design templates: the built-in ones and the user's own."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.design_template import DesignTemplate


class DesignTemplateStore(Protocol):
    def all(self) -> list[DesignTemplate]:
        """The user's saved ones first (newest first), then the built-ins."""
        ...

    def get(self, slug: str) -> DesignTemplate:
        """Raises KeyError naming the templates there are."""
        ...

    def html(self, template: DesignTemplate) -> str:
        """The template's HTML text."""
        ...

    def save(self, name: str, description: str, kind: str, tags: list[str], html_path: str, preview_path: str) -> DesignTemplate:
        """A slide's design kept as a template (its HTML and preview copied in)."""
        ...

    def delete(self, slug: str) -> None:
        """Only the user's own; a built-in cannot be deleted."""
        ...
