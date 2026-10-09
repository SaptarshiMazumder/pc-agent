"""BrandStore as one file in the workspace: brand.json."""

from __future__ import annotations

import json

from ad_generation.domain.brand_profile import BrandProfile
from ad_generation.infrastructure.run_workspace import RunWorkspace

_FILE = "posts/brand.json"


class BrandFileStore:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def get(self) -> BrandProfile:
        path = self._ws.path(_FILE)
        return BrandProfile.from_dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else BrandProfile()

    def save(self, brand: BrandProfile) -> None:
        path = self._ws.path(_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(brand.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
