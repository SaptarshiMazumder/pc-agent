"""Stories as files in the workspace: stories/<id>.json, one per story."""

from __future__ import annotations

import json

from ad_generation.domain.story import Story
from ad_generation.infrastructure.collection_file_store import slugify
from ad_generation.infrastructure.run_workspace import RunWorkspace

ROOT = "stories"


class StoryFileStore:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def all(self) -> list[Story]:
        folder = self._ws.path(ROOT)
        found = [Story.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in folder.glob("*.json")] if folder.is_dir() else []
        return sorted(found, key=lambda s: s.updated, reverse=True)

    def get(self, story_id: str) -> Story:
        path = self._ws.path(f"{ROOT}/{story_id}.json")
        if not path.is_file():
            known = ", ".join(s.id for s in self.all()) or "none yet"
            raise KeyError(f"no story '{story_id}' (stories: {known})")
        return Story.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def new_id(self, title: str) -> str:
        base = slugify(title)[:48].strip("-") or "story"
        taken = {s.id for s in self.all()}
        n = 1
        while f"{base}-{n:02d}" in taken:
            n += 1
        return f"{base}-{n:02d}"

    def save(self, story: Story) -> None:
        folder = self._ws.path(ROOT)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{story.id}.json.tmp"
        tmp.write_text(json.dumps(story.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(folder / f"{story.id}.json")
