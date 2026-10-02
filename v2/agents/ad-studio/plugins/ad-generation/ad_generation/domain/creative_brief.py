"""The plan for one ad: its format, concept and hook, ONE look shared by every shot (outfit,
place, time, weather, mood), and its shots in order."""

from __future__ import annotations

from dataclasses import dataclass, field

from ad_generation.domain.shot import Shot


@dataclass(frozen=True)
class CreativeBrief:
    format_key: str
    concept: str
    hook: str
    aspect_ratio: str  # "9:16" for Reels and Shorts
    shots: tuple[Shot, ...]
    caption: str
    look: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "CreativeBrief":
        shots = tuple(Shot.from_dict(s) for s in data.get("shots") or [] if isinstance(s, dict))
        if not shots:
            raise ValueError("the brief has no shots")
        ids = [s.id for s in shots]
        if len(set(ids)) != len(ids):
            raise ValueError("two shots share an id: " + ", ".join(ids))
        return cls(
            format_key=str(data.get("format_key") or "").strip(),
            concept=str(data.get("concept") or "").strip(),
            hook=str(data.get("hook") or "").strip(),
            aspect_ratio=str(data.get("aspect_ratio") or "9:16").strip(),
            shots=shots,
            caption=str(data.get("caption") or "").strip(),
            look=dict(data.get("look") or {}),
        )

    def shot(self, shot_id: str) -> Shot:
        for s in self.shots:
            if s.id == shot_id:
                return s
        raise KeyError(f"no shot '{shot_id}' in this brief (shots: {', '.join(s.id for s in self.shots)})")

    def to_dict(self) -> dict:
        return {
            "format_key": self.format_key,
            "concept": self.concept,
            "hook": self.hook,
            "aspect_ratio": self.aspect_ratio,
            "look": self.look,
            "shots": [s.to_dict() for s in self.shots],
            "caption": self.caption,
        }
