"""LibraryStore as files in the workspace: the user's uploads in library/, listed in
library/library.json (path, kind, label, when). Chat attachments in uploads/ stay where they are."""

from __future__ import annotations

import json
import shutil
import time

from ad_generation.domain.library_item import LibraryItem
from ad_generation.infrastructure.run_workspace import RunWorkspace

ROOT = "library"
_INDEX = f"{ROOT}/library.json"
_VIDEO = (".mp4", ".mov", ".webm")
_MEDIA = (".png", ".jpg", ".jpeg", ".webp", *_VIDEO)


class LibraryFileStore:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def uploads(self) -> list[LibraryItem]:
        return sorted(self._read(), key=lambda i: i.added, reverse=True)

    def add_upload(self, src: str, label: str) -> LibraryItem:
        source = self._ws.path(src)
        try:
            self._ws.rel(source)
        except ValueError:
            raise ValueError(f"{src} is outside the workspace") from None
        if not source.is_file():
            raise ValueError(f"no file {src}")
        if not source.name.lower().endswith(_MEDIA):
            raise ValueError(f"{source.name}: use an image (png, jpg, webp) or a clip (mp4, mov, webm)")
        folder = self._ws.path(ROOT)
        folder.mkdir(parents=True, exist_ok=True)
        target, n = folder / source.name, 2
        while target.exists():
            target, n = folder / f"{source.stem} ({n}){source.suffix}", n + 1
        shutil.copyfile(source, target)
        item = LibraryItem(
            path=f"{ROOT}/{target.name}", kind="video" if target.name.lower().endswith(_VIDEO) else "image",
            source="upload", product=label.strip(), added=time.time(),
        )
        self._write([*self._read(), item])
        return item

    def remove_upload(self, path: str) -> None:
        items = self._read()
        if path not in {i.path for i in items}:
            raise KeyError(f"{path} is not one of your uploads")
        self._ws.path(path).unlink(missing_ok=True)
        self._write([i for i in items if i.path != path])

    def _read(self) -> list[LibraryItem]:
        index = self._ws.path(_INDEX)
        return [LibraryItem.from_dict(d) for d in json.loads(index.read_text(encoding="utf-8"))] if index.is_file() else []

    def _write(self, items: list[LibraryItem]) -> None:
        index = self._ws.path(_INDEX)
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False), encoding="utf-8")
