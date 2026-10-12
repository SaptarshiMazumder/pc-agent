"""Occasions as JSON files shipped with the plugin: occasions/<key>.json."""

from __future__ import annotations

import json
from pathlib import Path

from ad_generation.domain.occasion import Occasion


class OccasionFileLibrary:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def all(self) -> list[Occasion]:
        return [self._load(p) for p in sorted(self._folder.glob("*.json"))]

    def get(self, key: str) -> Occasion:
        path = self._folder / f"{key}.json"
        if not path.is_file():
            known = ", ".join(p.stem for p in sorted(self._folder.glob("*.json")))
            raise KeyError(f"no occasion '{key}' (occasions: {known})")
        return self._load(path)

    @staticmethod
    def _load(path: Path) -> Occasion:
        return Occasion.from_dict(path.stem, json.loads(path.read_text(encoding="utf-8")))
