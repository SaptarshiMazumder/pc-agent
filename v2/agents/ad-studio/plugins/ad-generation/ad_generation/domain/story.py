"""A story — the direction of one campaign or post: who it is for, the occasion, the offer, the
tone, the hook, and the SHOT LIST of exactly what to make. Everything made for it follows it: the
prompts, the checks, the post's order, its words, its caption.

Loose by design: drafted from a theme (or the user's own words), and changed whenever the user
says. Every change bumps `version`; what was made records the version it was made for, so work
made for an older story is marked rather than mixed in. Under the hood each shot is made by a
template's stages — many stories share the same templates.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ad_generation.domain.story_shot import StoryShot

FORMATS = ("reel", "carousel")
STATUSES = ("draft", "approved")


@dataclass(frozen=True)
class Story:
    id: str
    title: str  # one line: "Not sure what to wear this Diwali? 3 looks"
    theme: str = ""  # the theme it was drafted from ("" = the user's own)
    occasion: str = ""  # an occasion key, or the user's words
    products: tuple[str, ...] = ()  # shop product keys
    audience: str = ""  # who it is for — and who buys, when that differs (a gift)
    goal: str = ""  # sell an offer | launch | trust | engagement
    offer: str = ""  # price, discount, combo, deadline — only what the user gave
    tone: str = ""
    cast: str = ""  # who is in it: a cast member's name, "none", or "" = not chosen yet
    language: str = ""  # English | Hindi | Hinglish | a regional language
    hook: str = ""  # the first words on screen
    format: str = "reel"
    shots: tuple[StoryShot, ...] = ()
    caption_angle: str = ""  # what the caption says, in a line
    cta: str = ""  # the one call to action: "DM to order", "Order on WhatsApp by Nov 3"
    notes: str = ""  # anything else the user said about it
    version: int = 1
    status: str = "draft"
    collection: str = ""  # the collection its picked results go into
    session: str = ""  # the chat it was made in — how the studio shows it
    auto_pick: bool = False  # Auto: the agent fills the shot list's unchosen counts, shapes, lengths
    created: float = 0.0
    updated: float = 0.0
    sources: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.format not in FORMATS:
            raise ValueError(f"story {self.id}: format is reel or carousel, not '{self.format}'")
        if self.status not in STATUSES:
            raise ValueError(f"story {self.id}: status is draft or approved, not '{self.status}'")
        ids = [s.id for s in self.shots]
        if len(ids) != len(set(ids)):
            raise ValueError(f"story {self.id}: two shots share an id")

    def shot(self, shot_id: str) -> StoryShot:
        found = next((s for s in self.shots if s.id == shot_id), None)
        if found is None:
            raise KeyError(f"story {self.id} has no shot '{shot_id}' (shots: {', '.join(s.id for s in self.shots)})")
        return found

    def unmade(self) -> dict[str, list[str]]:
        """Per shot, the choices still missing (empty when the shot list is ready)."""
        return {s.id: s.unmade() for s in self.shots if s.unmade()}

    def changed(self, now: float, **changes) -> "Story":
        """A new version of it — anything made before is for the old one. Approval is asked again."""
        return replace(self, **changes, version=self.version + 1, status="draft", updated=now)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "theme": self.theme, "occasion": self.occasion,
            "products": list(self.products), "audience": self.audience, "goal": self.goal, "offer": self.offer,
            "tone": self.tone, "cast": self.cast, "language": self.language, "hook": self.hook, "format": self.format,
            "shots": [s.to_dict() for s in self.shots], "caption_angle": self.caption_angle, "cta": self.cta,
            "notes": self.notes, "version": self.version, "status": self.status, "collection": self.collection, "session": self.session,
            "auto_pick": self.auto_pick, "created": self.created, "updated": self.updated, "sources": list(self.sources),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Story":
        return cls(
            id=str(data["id"]), title=str(data.get("title") or ""), theme=str(data.get("theme") or ""),
            occasion=str(data.get("occasion") or ""), products=tuple(str(p) for p in data.get("products") or ()),
            audience=str(data.get("audience") or ""), goal=str(data.get("goal") or ""), offer=str(data.get("offer") or ""),
            tone=str(data.get("tone") or ""), cast=str(data.get("cast") or ""), language=str(data.get("language") or ""), hook=str(data.get("hook") or ""),
            format=str(data.get("format") or "reel"), shots=tuple(StoryShot.from_dict(s) for s in data.get("shots") or ()),
            caption_angle=str(data.get("caption_angle") or ""), cta=str(data.get("cta") or ""), notes=str(data.get("notes") or ""),
            version=int(data.get("version") or 1), status=str(data.get("status") or "draft"),
            collection=str(data.get("collection") or ""), session=str(data.get("session") or ""), auto_pick=bool(data.get("auto_pick")),
            created=float(data.get("created") or 0), updated=float(data.get("updated") or 0),
            sources=tuple(str(s) for s in data.get("sources") or ()),
        )
