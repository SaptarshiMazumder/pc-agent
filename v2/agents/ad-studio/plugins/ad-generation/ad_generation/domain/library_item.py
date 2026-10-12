"""One picture or clip in the account's library: everything any campaign made, and the user's own
files uploaded there. The library is where they are browsed and picked into collections."""

from __future__ import annotations

from dataclasses import asdict, dataclass

KINDS = ("image", "video")
SOURCES = ("campaign", "upload")


@dataclass(frozen=True)
class LibraryItem:
    path: str  # workspace path
    kind: str  # "image" | "video"
    source: str  # "campaign" (a generation) | "upload" (the user's own file)
    campaign: str = ""  # the campaign it was made in; "" for an upload
    product: str = ""  # the campaign's product; for an upload, the label the user gave (may be "")
    added: float = 0.0  # when it was made or uploaded

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.path}: kind is image or video, not '{self.kind}'")
        if self.source not in SOURCES:
            raise ValueError(f"{self.path}: source is campaign or upload, not '{self.source}'")

    @classmethod
    def from_dict(cls, data: dict) -> "LibraryItem":
        return cls(
            path=str(data["path"]), kind=str(data["kind"]), source=str(data["source"]),
            campaign=str(data.get("campaign") or ""), product=str(data.get("product") or ""),
            added=float(data.get("added") or 0),
        )

    def to_dict(self) -> dict:
        return asdict(self)
