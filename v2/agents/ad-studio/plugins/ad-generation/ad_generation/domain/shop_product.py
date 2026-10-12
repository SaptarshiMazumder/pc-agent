"""A product in the shop's catalogue — set up once, picked for any story: its photos (the main one
first; angles; worn or in use), what it is, its price."""

from __future__ import annotations

from dataclasses import asdict, dataclass

KINDS = ("wear", "jewellery", "other")


@dataclass(frozen=True)
class ShopProduct:
    key: str
    title: str
    kind: str = "other"  # wear | jewellery | other — which stories suit it
    description: str = ""
    price: str = ""  # as the seller writes it: "₹1,499", "₹899 (MRP ₹1,999)"
    photos: tuple[str, ...] = ()  # workspace paths; the first is the main photo
    angles: tuple[str, ...] = ()
    in_use: tuple[str, ...] = ()  # worn / held / in use
    added: float = 0.0

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("a product needs a title")
        if self.kind not in KINDS:
            raise ValueError(f"product {self.title}: kind is one of {', '.join(KINDS)}, not '{self.kind}'")

    def all_photos(self) -> tuple[str, ...]:
        return self.photos + self.angles + self.in_use

    def to_dict(self) -> dict:
        return {**asdict(self), "photos": list(self.photos), "angles": list(self.angles), "in_use": list(self.in_use)}

    @classmethod
    def from_dict(cls, data: dict) -> "ShopProduct":
        return cls(
            key=str(data["key"]), title=str(data.get("title") or ""), kind=str(data.get("kind") or "other"),
            description=str(data.get("description") or ""), price=str(data.get("price") or ""),
            photos=tuple(str(p) for p in data.get("photos") or ()), angles=tuple(str(p) for p in data.get("angles") or ()),
            in_use=tuple(str(p) for p in data.get("in_use") or ()), added=float(data.get("added") or 0),
        )
