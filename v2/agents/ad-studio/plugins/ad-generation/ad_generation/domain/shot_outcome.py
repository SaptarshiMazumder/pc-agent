"""Where one shot of a campaign stands: its passing stills, the one chosen, its clip."""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class ShotOutcome:
    shot_id: str
    # "stills ready" | "still failed" | "clip ready" | "clip failed" | "not reached"
    status: str
    stills: dict[str, int] = field(default_factory=dict)  # every still that passed -> its score
    still: str = ""  # the chosen one: the best scorer until the user picks another
    clip: str = ""
    # "passed" | "failed" | "unchecked" (the provider returned no last frame) |
    # "refused" (the provider would not take the still; the reason is in problems) | ""
    clip_check: str = ""
    problems: tuple[str, ...] = ()

    @property
    def still_score(self) -> int:
        return self.stills.get(self.still, 0)

    @property
    def alternatives(self) -> tuple[str, ...]:
        return tuple(p for p in self.stills if p != self.still)

    def picked(self, path: str) -> "ShotOutcome":
        """The same shot with the user's still chosen — one of the stills that passed."""
        if path not in self.stills:
            raise ValueError(
                f"{self.shot_id}: '{path}' is not one of its passing stills ({', '.join(self.stills) or 'none'})"
            )
        return replace(self, still=path)

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "status": self.status,
            "stills": dict(self.stills),
            "still": self.still,
            "clip": self.clip,
            "clip_check": self.clip_check,
            "problems": list(self.problems),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ShotOutcome":
        return cls(
            shot_id=str(data["shot_id"]),
            status=str(data["status"]),
            stills={str(k): int(v) for k, v in (data.get("stills") or {}).items()},
            still=str(data.get("still") or ""),
            clip=str(data.get("clip") or ""),
            clip_check=str(data.get("clip_check") or ""),
            problems=tuple(data.get("problems") or ()),
        )
