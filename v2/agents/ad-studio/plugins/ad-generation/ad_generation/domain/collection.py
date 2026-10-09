"""A collection: images and clips picked from any campaigns' generations, gathered for a post.

It belongs to the account, not to a campaign — one post often shows several products, each made
in its own campaign. Campaign items point at the campaign files; the user's own files (made
elsewhere) are copied into the collection. Each product names where it was found — what the
caption credits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

KINDS = ("image", "video")


@dataclass(frozen=True)
class CollectionItem:
    path: str  # workspace path of the image or clip
    kind: str  # "image" | "video"
    campaign: str  # the campaign it was made in; "" for the user's own file
    product: str  # that campaign's product name — what the post says it is
    note: str = ""  # the user's note on it ("the best drape shot")

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.path}: kind is image or video, not '{self.kind}'")

    @classmethod
    def from_dict(cls, data: dict) -> "CollectionItem":
        return cls(
            path=str(data["path"]),
            kind=str(data["kind"]),
            campaign=str(data.get("campaign") or ""),
            product=str(data.get("product") or ""),
            note=str(data.get("note") or ""),
        )


@dataclass(frozen=True)
class ProductSource:
    """A product in the collection and where it was found — only what the user gave."""

    name: str
    found_at: str = ""  # the store, e.g. "PC Chandra Jewellers"
    link: str = ""  # the product page
    price: str = ""  # as the user wrote it, e.g. "₹1,93,696"

    @classmethod
    def from_dict(cls, data: dict) -> "ProductSource":
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("a product needs its name")
        return cls(name=name, **{k: str(data.get(k) or "").strip() for k in ("found_at", "link", "price")})


@dataclass
class Collection:
    slug: str
    name: str
    items: list[CollectionItem] = field(default_factory=list)
    created: float = 0.0
    products: list[ProductSource] = field(default_factory=list)

    def set_product(self, product: ProductSource) -> None:
        """Add the product, or replace what is known about it (by name)."""
        self.products = [p for p in self.products if p.name != product.name] + [product]

    def know(self, name: str) -> None:
        """A product is listed once its first item is in, even before its source is known."""
        if name and name not in {p.name for p in self.products}:
            self.products.append(ProductSource(name=name))

    def add(self, items: list[CollectionItem]) -> None:
        """Added at the end; an item already in it is not added twice."""
        have = {i.path for i in self.items}
        self.items += [i for i in items if i.path not in have]

    def remove(self, paths: list[str]) -> None:
        self.items = [i for i in self.items if i.path not in set(paths)]

    def reorder(self, paths: list[str]) -> None:
        """The items in this order; every item must be named once."""
        by_path = {i.path: i for i in self.items}
        if sorted(paths) != sorted(by_path):
            raise ValueError("a new order names every item of the collection once")
        self.items = [by_path[p] for p in paths]

    @classmethod
    def from_dict(cls, data: dict) -> "Collection":
        return cls(
            slug=str(data["slug"]),
            name=str(data.get("name") or data["slug"]),
            items=[CollectionItem.from_dict(i) for i in data.get("items") or []],
            created=float(data.get("created") or 0.0),
            products=[ProductSource.from_dict(p) for p in data.get("products") or []],
        )

    def to_dict(self) -> dict:
        return {
            "slug": self.slug, "name": self.name, "items": [asdict(i) for i in self.items], "created": self.created,
            "products": [asdict(p) for p in self.products],
        }
