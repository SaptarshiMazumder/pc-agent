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
    # Its real size, in words ("about 9 cm tall, fits in a palm") — what keeps a bottle from
    # turning into a jug in a hand. Empty when the photos give no clue.
    size: str = ""
    # The words printed on the product, exactly, as far as they read on a phone screen — what an
    # image model garbles first and a checker reads back. Empty when it has none.
    label_text: tuple[str, ...] = ()

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
            size=str(data.get("size") or "").strip(),
            label_text=_texts(data.get("label_text")),
        )

    @classmethod
    def load(cls, data: dict) -> "ProductProfile":
        return cls.from_dict(data, tuple(data.get("photos") or ()))

    def scale_and_label(self) -> str:
        """The size and the label, as sentences for any prompt that shows the product ("" when
        the reader found neither)."""
        parts = []
        if self.size:
            parts.append(f"Its real size: {self.size} — it stays that size beside hands, faces and things around it.")
        if self.label_text:
            words = " / ".join(f'"{t}"' for t in self.label_text)
            parts.append(f"Its label reads exactly {words} — letter for letter, never changed, invented or blurred.")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)
