"""Where a post's design run is, slide by slide — what the window shows while it works, so the user
sees it move: which slides are being designed, which round, the latest preview, what the art
director still wants fixed, and which are done."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

STATES = ("waiting", "designing", "reviewing", "done", "failed", "stopped")


@dataclass
class SlideDesignState:
    state: str = "waiting"
    round: int = 0
    rounds: int = 0
    preview: str = ""  # the latest round's render (overwritten each round)
    problems: list[str] = field(default_factory=list)
    reference: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"a slide's design state is one of {', '.join(STATES)}, not '{self.state}'")


@dataclass
class DesignProgress:
    post: str
    active: bool
    started: float
    updated: float
    slides: dict[int, SlideDesignState] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "DesignProgress":
        return cls(
            post=str(data["post"]), active=bool(data.get("active")), started=float(data.get("started") or 0),
            updated=float(data.get("updated") or 0),
            slides={int(n): SlideDesignState(**s) for n, s in (data.get("slides") or {}).items()},
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "slides": {str(n): asdict(s) for n, s in self.slides.items()}}
