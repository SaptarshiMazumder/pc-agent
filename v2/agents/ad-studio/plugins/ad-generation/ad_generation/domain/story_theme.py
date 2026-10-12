"""A story theme — a default story the user can start from — DATA, a JSON file (stories/<key>.json):
what it says, the hooks that work, its shot list with suggested counts, shapes and lengths, the
caption pattern, the call to action, and the questions a seller must answer for it. Researched
from real campaigns (`sources`). Loose: the agent adapts it to the products, the occasion and the
user's words."""

from __future__ import annotations

from dataclasses import dataclass

from ad_generation.domain.story_shot import StoryShot

KINDS = ("wear", "jewellery")


@dataclass(frozen=True)
class StoryTheme:
    key: str
    name: str
    summary: str
    kinds: tuple[str, ...]  # which products it suits: wear, jewellery
    occasions: tuple[str, ...]  # occasion keys it fits; ("evergreen",) for any time
    goal: str
    hooks: tuple[str, ...]  # example first lines
    format: str  # reel | carousel
    shots: tuple[StoryShot, ...]  # its default shot list; counts/shapes/lengths only as suggestions
    caption: str  # the caption pattern
    cta: str
    asks: tuple[str, ...]  # what to know before drafting: occasion, offer, audience, tone, products…
    why: str
    sources: tuple[str, ...]

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "StoryTheme":
        for k in ("name", "summary", "shots", "format"):
            if not data.get(k):
                raise ValueError(f"story theme {key} has no '{k}'")
        bad = [k for k in data.get("kinds") or () if k not in KINDS]
        if bad:
            raise ValueError(f"story theme {key}: kinds are {', '.join(KINDS)}, not {', '.join(bad)}")
        shots = tuple(
            StoryShot.from_dict({
                "id": f"s{i}", "shows": s.get("shows"), "purpose": s.get("purpose"), "kind": s.get("kind", "image"),
                "product": s.get("product", ""), "text": s.get("text", ""),
                "suggested_count": s.get("count", 0), "suggested_aspect": s.get("aspect", ""),
                "suggested_seconds": s.get("seconds", 0),
            })
            for i, s in enumerate(data["shots"], 1)
        )
        return cls(
            key=key, name=str(data["name"]), summary=str(data["summary"]),
            kinds=tuple(str(k) for k in data.get("kinds") or KINDS),
            occasions=tuple(str(o) for o in data.get("occasions") or ("evergreen",)),
            goal=str(data.get("goal") or ""), hooks=tuple(str(h) for h in data.get("hooks") or ()), format=str(data["format"]),
            shots=shots, caption=str(data.get("caption") or ""), cta=str(data.get("cta") or ""),
            asks=tuple(str(a) for a in data.get("asks") or ()), why=str(data.get("why") or ""),
            sources=tuple(str(s) for s in data.get("sources") or ()),
        )

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "summary": self.summary, "kinds": list(self.kinds),
            "occasions": list(self.occasions), "goal": self.goal, "hooks": list(self.hooks), "format": self.format,
            "shots": [s.to_dict() for s in self.shots], "caption": self.caption, "cta": self.cta, "asks": list(self.asks),
            "why": self.why, "sources": list(self.sources),
        }
