"""A place to shoot in — DATA, a JSON file (locations/<key>.json): the short name the user picks,
and the words the brief and the image prompt get for it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShootLocation:
    key: str
    name: str  # what the user sees and picks ("Jaipur haveli courtyard")
    prompt: str  # what the brief gets: the place described so a model can draw it
    tags: tuple[str, ...]  # festive, wedding, heritage, everyday, studio…

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "ShootLocation":
        for k in ("name", "prompt"):
            if not data.get(k):
                raise ValueError(f"location {key} has no '{k}'")
        return cls(key=key, name=str(data["name"]), prompt=str(data["prompt"]), tags=tuple(str(t) for t in data.get("tags") or ()))
