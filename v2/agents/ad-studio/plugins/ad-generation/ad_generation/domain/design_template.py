"""A design template: a slide design (HTML/CSS) kept to start new designs from — the agent looks at
its preview, picks it, and adapts it to a post's pictures and words. Built-in ones ship with the
plugin; the user's own are saved from slides they liked."""

from __future__ import annotations

from dataclasses import asdict, dataclass

KINDS = ("still", "video")  # video: the design has a slot a clip plays in


@dataclass(frozen=True)
class DesignTemplate:
    slug: str
    name: str
    description: str  # what it is for, in a line — "a sale poster: the offer huge, the product framed"
    kind: str
    html: str  # path of its HTML (plugin-relative for built-ins, workspace-relative for saved ones)
    thumb: str  # path of its preview
    tags: tuple[str, ...] = ()
    origin: str = "builtin"  # "builtin" | "saved"

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"a template is a {' or '.join(KINDS)} design, not '{self.kind}'")

    @classmethod
    def from_dict(cls, data: dict) -> "DesignTemplate":
        return cls(
            slug=str(data["slug"]), name=str(data.get("name") or data["slug"]), description=str(data.get("description") or ""),
            kind=str(data.get("kind") or "still"), html=str(data["html"]), thumb=str(data.get("thumb") or ""),
            tags=tuple(str(t) for t in data.get("tags") or ()), origin=str(data.get("origin") or "builtin"),
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "tags": list(self.tags)}
