"""A design reference: a picture of a design the agent (or the user) liked — a Canva template's
preview, a post seen elsewhere, a screenshot — kept with what a designer reads off it: the layout,
how the photos are shaped, the type, the palette, the decoration, where the words go. New designs
follow its STRUCTURE with the post's own pictures and words; the picture itself is never used in
a post, and its words, logos and photos are never copied."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# What a designer reads off a reference — the keys of `spec`.
SPEC_KEYS = ("layout", "photos", "type", "palette", "decoration", "text_slots", "mood")


@dataclass(frozen=True)
class DesignReference:
    slug: str
    name: str
    image: str  # workspace path of its picture
    source: str = ""  # where it was found: a link, or "your screenshot"
    suits: tuple[str, ...] = ()  # what it is good for — "sale", "new collection", "festive", "product grid"
    spec: dict = field(default_factory=dict)
    created: float = 0.0
    fingerprint: str = ""  # of its picture: the same picture is never kept twice

    @classmethod
    def from_dict(cls, data: dict) -> "DesignReference":
        return cls(
            slug=str(data["slug"]), name=str(data.get("name") or data["slug"]), image=str(data["image"]),
            source=str(data.get("source") or ""), suits=tuple(str(t) for t in data.get("suits") or ()),
            spec={k: str(v) for k, v in (data.get("spec") or {}).items() if k in SPEC_KEYS},
            created=float(data.get("created") or 0), fingerprint=str(data.get("fingerprint") or ""),
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "suits": list(self.suits)}
