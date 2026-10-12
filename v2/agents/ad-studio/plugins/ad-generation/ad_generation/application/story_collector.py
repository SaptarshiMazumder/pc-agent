"""A story's picked results, gathered: every result the user picks in one of the story's campaigns
goes into the story's collection — made the first time, named after the story — so the post is
built from exactly what was chosen. A video's first frame is only a step towards its clip: its
picks stay out (the clip goes in)."""

from __future__ import annotations

from dataclasses import replace

from ad_generation.application.collection_service import CollectionService
from ad_generation.application.interfaces.story_store import StoryStore


class StoryCollector:
    def __init__(self, stories: StoryStore, collections: CollectionService) -> None:
        self._stories = stories
        self._collections = collections

    def picked(self, story_id: str, campaign_id: str, step_id: str, path: str) -> str:
        """-> the collection's slug ("" for a first frame, which stays out)."""
        if step_id.endswith("-frame"):
            return ""
        story = self._stories.get(story_id)
        if story.collection:
            found = self._collections.get(story.collection)
            if path in {i.path for i in found.items}:
                return found.slug
            return self._collections.add(found.slug, [{"path": path, "campaign": campaign_id}]).slug
        made = self._collections.add(story.title, [{"path": path, "campaign": campaign_id}], new=True)
        self._stories.save(replace(story, collection=made.slug))
        return made.slug
