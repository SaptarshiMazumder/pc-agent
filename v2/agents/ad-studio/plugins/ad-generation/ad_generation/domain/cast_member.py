"""A recurring AI model: the same face across every ad is what makes an account followable."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CastMember:
    name: str  # a slug, e.g. "mira"
    description: str  # face, hair, build, age range, style — what every prompt restates
    sheet: str  # workspace path of the character sheet image every keyframe references

    @classmethod
    def from_dict(cls, data: dict) -> "CastMember":
        return cls(name=str(data["name"]), description=str(data["description"]), sheet=str(data["sheet"]))

    def to_dict(self) -> dict:
        return asdict(self)
