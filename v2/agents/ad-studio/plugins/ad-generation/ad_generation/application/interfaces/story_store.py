"""The account's stories."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.story import Story


class StoryStore(Protocol):
    def all(self) -> list[Story]: ...

    def get(self, story_id: str) -> Story:
        """Raises KeyError naming the stories there are."""
        ...

    def new_id(self, title: str) -> str: ...

    def save(self, story: Story) -> None: ...
