"""CollectionStore as files: collections/<slug>/collection.json."""

from __future__ import annotations

import json
import re
import shutil

from ad_generation.domain.collection import Collection
from ad_generation.infrastructure.run_workspace import RunWorkspace

ROOT = "collections"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "untitled"


class CollectionFileStore:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def all(self) -> list[Collection]:
        root = self._ws.path(ROOT)
        if not root.is_dir():
            return []
        found = [Collection.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in root.glob("*/collection.json")]
        return sorted(found, key=lambda c: c.created, reverse=True)

    def get(self, slug: str) -> Collection:
        path = self._ws.path(f"{ROOT}/{slug}/collection.json")
        if not path.is_file():
            known = ", ".join(c.slug for c in self.all()) or "none yet"
            raise KeyError(f"no collection '{slug}' (collections: {known})")
        return Collection.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def find(self, name_or_slug: str) -> Collection | None:
        key = name_or_slug.strip().lower()
        return next((c for c in self.all() if c.slug == key or c.name.lower() == key or c.slug == slugify(key)), None)

    def new_slug(self, name: str) -> str:
        base = slugify(name)
        slug, n = base, 2
        while self._ws.path(f"{ROOT}/{slug}").exists():
            slug, n = f"{base}-{n}", n + 1
        return slug

    def save(self, collection: Collection) -> None:
        path = self._ws.path(f"{ROOT}/{collection.slug}/collection.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(collection.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def import_file(self, slug: str, src: str) -> str:
        source = self._ws.path(src)
        if not source.is_file():
            raise ValueError(f"no file {src} to add")
        folder = self._ws.path(f"{ROOT}/{slug}/files")
        folder.mkdir(parents=True, exist_ok=True)
        target, n = folder / source.name, 2
        while target.exists():
            target, n = folder / f"{source.stem} ({n}){source.suffix}", n + 1
        shutil.copyfile(source, target)
        return f"{ROOT}/{slug}/files/{target.name}"

    def delete(self, slug: str) -> None:
        path = self._ws.path(f"{ROOT}/{slug}/collection.json")
        if not path.is_file():
            raise KeyError(f"no collection '{slug}'")
        shutil.rmtree(path.parent)
