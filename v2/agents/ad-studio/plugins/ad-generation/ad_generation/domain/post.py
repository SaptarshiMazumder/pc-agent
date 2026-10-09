"""An Instagram post made from a collection: its slides in order, the words on each, how each clip
is cut, and the caption.

A slide shows ONE image or clip full-bleed; text is laid over it (TextCue), never in a panel. A
cue can fade in and out while the clip keeps playing, and can move (the end card's first line
rising to make room for the tagline). A still whose text moves is rendered as a short video —
Instagram carousels take both. A Reel is the slides one after another, at 9:16.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ad_generation.domain.instagram_format import FORMATS, MAX_SLIDES

FACES = ("heading", "label")  # the brand's heading or label type
ALIGNS = ("left", "center", "right")


@dataclass(frozen=True)
class TextCue:
    text: str
    y: float = 0.85  # where the text's middle sits, as a fraction of the height
    face: str = "heading"
    size: float = 0.05  # letter size, a fraction of the height (shrunk to fit the width)
    align: str = "center"
    start: float = 0.0  # seconds: when it begins to appear (a still: always shown)
    end: float = 0.0  # seconds: when it has gone; 0 = it stays to the end
    fade_in: float = 0.4
    fade_out: float = 0.6
    to_y: float = -1.0  # where it moves to; -1 = it does not move
    move_at: float = 0.0  # seconds: when the move starts
    move_s: float = 0.6  # how long the move takes
    color: str = ""  # "" = the brand's text colour

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("a text cue needs its words")
        if self.face not in FACES or self.align not in ALIGNS:
            raise ValueError(f"'{self.text[:30]}': face is heading/label and align left/center/right")
        if not (0 < self.y < 1) or not (0 < self.size <= 0.2):
            raise ValueError(f"'{self.text[:30]}': y is a fraction of the height and size up to 0.2")
        if self.end and self.end <= self.start:
            raise ValueError(f"'{self.text[:30]}': it must end after it starts")

    @property
    def moves(self) -> bool:
        return 0 < self.to_y < 1

    @property
    def animated(self) -> bool:
        """It changes over time — a still carrying it becomes a short video."""
        return self.moves or self.start > 0 or self.end > 0

    @classmethod
    def from_dict(cls, data: dict) -> "TextCue":
        fields = cls.__dataclass_fields__
        return cls(**{k: (str(v) if fields[k].type == "str" else float(v)) for k, v in data.items() if k in fields})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class ClipEdit:
    speed: float = 1.0  # 1.25 = a quarter faster
    trim_start: float = 0.0  # seconds cut from the start
    trim_end: float = 0.0  # seconds cut from the end
    fade_in: float = 0.0  # seconds, from black
    fade_out: float = 0.5  # seconds, to black — so a clip never cuts off abruptly

    def __post_init__(self) -> None:
        if not 0.25 <= self.speed <= 4:
            raise ValueError(f"speed is 0.25-4, not {self.speed}")
        if min(self.trim_start, self.trim_end, self.fade_in, self.fade_out) < 0:
            raise ValueError("trims and fades are seconds, not negative")

    @classmethod
    def from_dict(cls, data: dict) -> "ClipEdit":
        return cls(**{k: float(v) for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class Slide:
    item: str  # workspace path of the image or clip it shows
    kind: str  # "image" | "video"
    cues: tuple[TextCue, ...] = ()
    edit: ClipEdit = field(default_factory=ClipEdit)  # a clip's cut (a still ignores it)
    seconds: float = 4.0  # how long a still runs when it becomes video (animated text, a Reel)
    note: str = ""  # what it is for — "product 1, the model", "end card"
    # Its DESIGN, when it has one: the agent's HTML/CSS for this slide (workspace path) and the
    # still of it last rendered. A designed slide is rendered from its design; a plain one is the
    # photo or clip with its words over it.
    design: str = ""
    preview: str = ""
    reference: str = ""  # the design reference its design follows (slug), when it follows one

    def words(self) -> tuple[str, ...]:
        return tuple(" ".join(c.text.split()) for c in self.cues)

    def with_design_of(self, before: "Slide") -> "Slide":
        """This slide keeping the design `before` had — when it shows the same picture with the same
        words, the design still fits; anything else needs designing again."""
        if before.design and before.item == self.item and before.words() == self.words():
            return Slide.from_dict({**self.to_dict(), "design": before.design, "preview": before.preview, "reference": before.reference})
        return self

    @property
    def as_video(self) -> bool:
        return self.kind == "video" or any(c.animated for c in self.cues)

    @classmethod
    def from_dict(cls, data: dict) -> "Slide":
        return cls(
            item=str(data["item"]),
            kind=str(data["kind"]),
            cues=tuple(TextCue.from_dict(c) for c in data.get("cues") or []),
            edit=ClipEdit.from_dict(data.get("edit") or {}),
            seconds=float(data.get("seconds") or 4.0),
            note=str(data.get("note") or ""),
            design=str(data.get("design") or ""),
            preview=str(data.get("preview") or ""),
            reference=str(data.get("reference") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "item": self.item, "kind": self.kind, "cues": [c.to_dict() for c in self.cues],
            "edit": self.edit.to_dict(), "seconds": self.seconds, "note": self.note,
            "design": self.design, "preview": self.preview, "reference": self.reference,
        }


@dataclass
class Post:
    slug: str
    name: str
    collection: str
    format: str = "carousel"
    slides: list[Slide] = field(default_factory=list)
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    session: str = ""  # the chat it is made in
    rendered: list[str] = field(default_factory=list)  # the files of the last render, in order
    rendered_at: float = 0.0
    # Who made the files: "renderer" (our own, from the slides) or "canva" (a Canva template the
    # agent filled in the browser and downloaded); with Canva, the design's link to open it there.
    design: str = "renderer"
    canva_url: str = ""

    def check(self) -> None:
        if self.format not in FORMATS:
            raise ValueError(f"a post is a {' or a '.join(FORMATS)}, not '{self.format}'")
        if not self.slides:
            raise ValueError("a post needs at least one slide")
        if len(self.slides) > MAX_SLIDES[self.format]:
            raise ValueError(f"an Instagram {self.format} holds up to {MAX_SLIDES[self.format]} slides, not {len(self.slides)}")

    def with_slides(self, slides: list[Slide]) -> "Post":
        return replace(self, slides=list(slides))

    @classmethod
    def from_dict(cls, data: dict) -> "Post":
        return cls(
            slug=str(data["slug"]),
            name=str(data.get("name") or data["slug"]),
            collection=str(data.get("collection") or ""),
            format=str(data.get("format") or "carousel"),
            slides=[Slide.from_dict(s) for s in data.get("slides") or []],
            caption=str(data.get("caption") or ""),
            hashtags=[str(h).lstrip("#") for h in data.get("hashtags") or []],
            session=str(data.get("session") or ""),
            rendered=[str(r) for r in data.get("rendered") or []],
            rendered_at=float(data.get("rendered_at") or 0.0),
            design=str(data.get("design") or "renderer"),
            canva_url=str(data.get("canva_url") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "slug": self.slug, "name": self.name, "collection": self.collection, "format": self.format,
            "slides": [s.to_dict() for s in self.slides], "caption": self.caption, "hashtags": list(self.hashtags),
            "session": self.session, "rendered": list(self.rendered), "rendered_at": self.rendered_at,
            "design": self.design, "canva_url": self.canva_url,
        }
