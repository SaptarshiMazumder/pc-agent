"""The shop's product catalogue."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.shop_product import ShopProduct


class ShopProductStore(Protocol):
    def all(self) -> list[ShopProduct]: ...

    def get(self, key: str) -> ShopProduct:
        """Raises KeyError naming the products there are."""
        ...

    def new_key(self, title: str) -> str: ...

    def save(self, product: ShopProduct) -> None: ...

    def remove(self, key: str) -> None: ...
