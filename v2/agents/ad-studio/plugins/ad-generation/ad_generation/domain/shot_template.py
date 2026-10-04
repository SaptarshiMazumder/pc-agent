"""One fixed shot of a recipe: its role and framing are decided here; only the scene varies."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ShotTemplate:
    id: str
    purpose: str  # hook / detail / payoff / ...
    direction: str  # what the shot must show and how it is framed — the brief fills the scene in
    cast: bool  # a cast member appears
    shows_product: bool
    duration_s: int

    @classmethod
    def from_dict(cls, data: dict) -> "ShotTemplate":
        missing = [k for k in ("id", "purpose", "direction") if not data.get(k)]
        if missing:
            raise ValueError(f"recipe shot {data.get('id') or '?'} has no " + ", ".join(missing))
        return cls(
            id=str(data["id"]),
            purpose=str(data["purpose"]),
            direction=str(data["direction"]),
            cast=bool(data.get("cast", False)),
            shows_product=bool(data.get("shows_product", True)),
            duration_s=int(data.get("duration_s", 5)),
        )

    def to_dict(self) -> dict:
        return asdict(self)
