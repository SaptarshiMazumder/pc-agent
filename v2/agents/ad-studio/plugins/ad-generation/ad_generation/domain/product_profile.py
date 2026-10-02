"""What a product IS, read off its photos — the facts every later step must not contradict."""

from __future__ import annotations

from dataclasses import asdict, dataclass


def _texts(value) -> tuple[str, ...]:
    return tuple(str(v).strip() for v in value or [] if str(v).strip())


@dataclass(frozen=True)
class ProductProfile:
    name: str
    category: str
    description: str
    # The details a generated image must reproduce exactly (logo, hardware, stitching, print).
    # A still that loses one is a still of a different product.
    must_keep: tuple[str, ...]
    materials: tuple[str, ...]
    colors: tuple[str, ...]
    audience: str
    price_tier: str
    # Workspace-relative paths of the product photos this profile was read from.
    photos: tuple[str, ...]
    # The recipe this kind of product is made with (recipes/<key>.json), as the reader matched it.
    recipe: str = ""

    @classmethod
    def from_dict(cls, data: dict, photos: tuple[str, ...]) -> "ProductProfile":
        missing = [k for k in ("name", "category", "description", "must_keep") if not data.get(k)]
        if missing:
            raise ValueError("the product analysis left out: " + ", ".join(missing))
        return cls(
            name=str(data["name"]).strip(),
            category=str(data["category"]).strip(),
            description=str(data["description"]).strip(),
            must_keep=_texts(data.get("must_keep")),
            materials=_texts(data.get("materials")),
            colors=_texts(data.get("colors")),
            audience=str(data.get("audience") or "").strip(),
            price_tier=str(data.get("price_tier") or "").strip(),
            photos=tuple(photos),
            recipe=str(data.get("recipe") or "").strip(),
        )

    @classmethod
    def load(cls, data: dict) -> "ProductProfile":
        return cls.from_dict(data, tuple(data.get("photos") or ()))

    def to_dict(self) -> dict:
        return asdict(self)
