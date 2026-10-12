"""The product catalogue as one file in the workspace: products/products.json. Photos stay where
they are (the library, uploads); the catalogue holds their paths."""

from __future__ import annotations

import json

from ad_generation.domain.shop_product import ShopProduct
from ad_generation.infrastructure.collection_file_store import slugify
from ad_generation.infrastructure.run_workspace import RunWorkspace

_FILE = "products/products.json"


class ShopProductFileStore:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def all(self) -> list[ShopProduct]:
        path = self._ws.path(_FILE)
        rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        return sorted((ShopProduct.from_dict(r) for r in rows), key=lambda p: p.added, reverse=True)

    def get(self, key: str) -> ShopProduct:
        found = next((p for p in self.all() if p.key == key), None)
        if found is None:
            known = ", ".join(p.key for p in self.all()) or "none yet"
            raise KeyError(f"no product '{key}' (products: {known})")
        return found

    def new_key(self, title: str) -> str:
        base = slugify(title)[:48].strip("-") or "product"
        taken = {p.key for p in self.all()}
        key, n = base, 2
        while key in taken:
            key, n = f"{base}-{n}", n + 1
        return key

    def save(self, product: ShopProduct) -> None:
        self._write([p for p in self.all() if p.key != product.key] + [product])

    def remove(self, key: str) -> None:
        self.get(key)
        self._write([p for p in self.all() if p.key != key])

    def _write(self, products: list[ShopProduct]) -> None:
        path = self._ws.path(_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([p.to_dict() for p in products], ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
