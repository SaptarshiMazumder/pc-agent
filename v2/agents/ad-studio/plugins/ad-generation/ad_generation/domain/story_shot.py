"""One row of a story's shot list: ONE thing to make — an image or a video — what it shows, why it
is in the story, which product, and its shape, count and length.

Manual by default: count, shape and length are the user's choices; empty (0 / "") until made. A
row's `suggested_*` values are what Auto puts there — never used with Auto off.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

KINDS = ("image", "video")
ASPECTS = ("9:16", "4:5", "1:1", "3:4", "16:9")


@dataclass(frozen=True)
class StoryShot:
    id: str  # "s1", "s2"… — stable, so results and edits stay tied to their row
    shows: str  # what is in the picture, in plain words
    purpose: str  # why it is in the story: "hook", "look 2 — family lunch", "price + call to action"
    kind: str  # image | video
    product: str = ""  # the shop product's key it shows; "" = none, "all" = every product of the story
    text: str = ""  # the words on screen for this shot ("" = none)
    count: int = 0  # how many to make; 0 = not chosen yet
    aspect: str = ""  # "" = not chosen yet
    seconds: int = 0  # videos: length; 0 = not chosen yet
    # The user's own image it starts from (a workspace path): a video animates it, an image is it —
    # nothing is generated in its place. "" = it is made.
    start_from: str = ""
    suggested_count: int = 0
    suggested_aspect: str = ""
    suggested_seconds: int = 0

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"shot {self.id}: kind is image or video, not '{self.kind}'")
        for a in (self.aspect, self.suggested_aspect):
            if a and a not in ASPECTS:
                raise ValueError(f"shot {self.id}: shape is one of {', '.join(ASPECTS)}, not '{a}'")
        if self.count < 0 or self.seconds < 0:
            raise ValueError(f"shot {self.id}: count and length cannot be negative")
        if self.kind == "image" and self.seconds:
            raise ValueError(f"shot {self.id}: an image has no length")

    def unmade(self) -> list[str]:
        """The choices still missing before it can be made."""
        missing = [] if self.count else ["how many"]
        missing += [] if self.aspect else ["shape"]
        if self.kind == "video" and not self.seconds:
            missing.append("length")
        return missing

    def with_suggestions(self) -> "StoryShot":
        """Auto: its suggestions filled into the choices nobody made."""
        return replace(
            self,
            count=self.count or self.suggested_count,
            aspect=self.aspect or self.suggested_aspect,
            seconds=self.seconds or (self.suggested_seconds if self.kind == "video" else 0),
        )

    def with_(self, **changes) -> "StoryShot":
        return replace(self, **changes)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StoryShot":
        return cls(
            id=str(data["id"]), shows=str(data.get("shows") or ""), purpose=str(data.get("purpose") or ""),
            kind=str(data.get("kind") or "image"), product=str(data.get("product") or ""),
            text=str(data.get("text") or ""), count=int(data.get("count") or 0), aspect=str(data.get("aspect") or ""),
            seconds=int(data.get("seconds") or 0), start_from=str(data.get("start_from") or ""),
            suggested_count=int(data.get("suggested_count") or 0),
            suggested_aspect=str(data.get("suggested_aspect") or ""), suggested_seconds=int(data.get("suggested_seconds") or 0),
        )
