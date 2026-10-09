"""The account's brand profile: read it, change some of it."""

from __future__ import annotations

from ad_generation.application.interfaces.brand_store import BrandStore
from ad_generation.domain.brand_profile import BrandProfile


class BrandService:
    def __init__(self, store: BrandStore) -> None:
        self._store = store

    def get(self) -> BrandProfile:
        return self._store.get()

    def update(self, changes: dict) -> BrandProfile:
        unknown = [k for k in changes if k not in BrandProfile.__dataclass_fields__]
        if unknown:
            raise ValueError(f"no brand field {', '.join(unknown)} (fields: {', '.join(BrandProfile.__dataclass_fields__)})")
        brand = BrandProfile.from_dict({**self._store.get().to_dict(), **changes})  # validates
        self._store.save(brand)
        return brand
