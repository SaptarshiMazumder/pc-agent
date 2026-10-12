"""Shoot locations as JSON files shipped with the plugin: locations/<key>.json."""

from __future__ import annotations

import json
from pathlib import Path

from ad_generation.domain.shoot_location import ShootLocation


class LocationFileLibrary:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def all(self) -> list[ShootLocation]:
        return [self._load(p) for p in sorted(self._folder.glob("*.json"))]

    def get(self, key: str) -> ShootLocation:
        path = self._folder / f"{key}.json"
        if not path.is_file():
            known = ", ".join(p.stem for p in sorted(self._folder.glob("*.json")))
            raise KeyError(f"no location '{key}' (locations: {known})")
        return self._load(path)

    @staticmethod
    def _load(path: Path) -> ShootLocation:
        return ShootLocation.from_dict(path.stem, json.loads(path.read_text(encoding="utf-8")))
