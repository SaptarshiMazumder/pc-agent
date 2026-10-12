"""The story themes — the default stories a campaign or post can start from."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.story_theme import StoryTheme


class StoryThemeLibrary(Protocol):
    def all(self) -> list[StoryTheme]: ...

    def get(self, key: str) -> StoryTheme:
        """Raises KeyError naming the themes there are."""
        ...
