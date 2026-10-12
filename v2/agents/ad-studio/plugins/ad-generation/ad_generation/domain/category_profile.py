"""A category of product the shop sells (saree, necklace, earrings…) — DATA, a JSON file
(categories/<key>.json): what a faithful picture of one must keep, how it is shot, where, and in
which poses. The product reader matches each product to one; its template, checks and shot
guidance then follow the product through the brief, the prompts and the accuracy check."""

from __future__ import annotations

from dataclasses import dataclass

KINDS = ("wear", "jewellery")


@dataclass(frozen=True)
class CategoryProfile:
    key: str
    name: str
    kind: str
    covers: str  # what it covers, in words — what the product reader matches against
    template: str  # the recipe (template) a product of this category is made with
    checks: tuple[str, ...]  # what a faithful picture keeps exactly — turned into the product's must_keep
    shot: str  # how it is framed so it reads
    locations: tuple[str, ...]  # location keys that suit it, best first
    poses: tuple[str, ...]  # poses (or framings) that show it

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "CategoryProfile":
        if data.get("kind") not in KINDS:
            raise ValueError(f"category {key}: kind is one of {', '.join(KINDS)}, not '{data.get('kind')}'")
        for k in ("name", "covers", "template", "checks", "shot"):
            if not data.get(k):
                raise ValueError(f"category {key} has no '{k}'")
        return cls(
            key=key,
            name=str(data["name"]),
            kind=str(data["kind"]),
            covers=str(data["covers"]),
            template=str(data["template"]),
            checks=tuple(str(c) for c in data["checks"]),
            shot=str(data["shot"]),
            locations=tuple(str(x) for x in data.get("locations") or ()),
            poses=tuple(str(x) for x in data.get("poses") or ()),
        )
