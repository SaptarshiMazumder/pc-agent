"""The shop's product catalogue: products set up once — photos, title, kind, price — and picked for
any story. Free and instant."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from ad_generation.application.interfaces.shop_product_store import ShopProductStore
from ad_generation.domain.shop_product import ShopProduct

_FIELDS = ("title", "kind", "description", "price", "photos", "angles", "in_use")


class ShopProductService:
    def __init__(self, store: ShopProductStore, clock: Callable[[], float]) -> None:
        self._store = store
        self._clock = clock

    def all(self) -> list[ShopProduct]:
        return self._store.all()

    def get(self, key: str) -> ShopProduct:
        return self._store.get(key)

    def save(self, data: dict) -> ShopProduct:
        """A new product (no `key`), or changes to one (its `key` and the fields to change)."""
        unknown = [k for k in data if k not in (*_FIELDS, "key")]
        if unknown:
            raise ValueError(f"a product has no {', '.join(unknown)} (fields: {', '.join(_FIELDS)})")
        changes = {k: tuple(data[k]) if k in ("photos", "angles", "in_use") else str(data[k]) for k in _FIELDS if k in data}
        key = str(data.get("key") or "")
        if key:
            product = replace(self._store.get(key), **changes)
        else:
            if not changes.get("photos"):
                raise ValueError("a new product needs at least one photo")
            product = ShopProduct(key=self._store.new_key(str(changes.get("title") or "")), added=self._clock(), **changes)
        self._store.save(product)
        return product

    def remove(self, key: str) -> None:
        self._store.remove(key)
