"""One shot of an ad: the still that fixes its look, and the motion that animates it.

`spec` holds the checklist values the prompts were composed from (pose, background, framing,
lighting, action, camera move) — kept so a redo can change one item and recompose.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Shot:
    id: str  # "s1", "s2" — stable within a brief
    purpose: str  # hook / product reveal / detail / payoff
    keyframe_prompt: str  # the still, composed from the look + this shot's spec
    motion_prompt: str  # the clip, composed the same way
    duration_s: int
    cast: tuple[str, ...]  # cast member names in this shot; empty for product-only shots
    shows_product: bool
    spec: dict = field(default_factory=dict)
    # A text ad's picture WITHOUT its words, for overlay mode; "" for a photo ad.
    overlay_prompt: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Shot":
        missing = [k for k in ("id", "keyframe_prompt", "motion_prompt") if not data.get(k)]
        if missing:
            raise ValueError(f"shot {data.get('id') or '?'} has no " + ", ".join(missing))
        return cls(
            id=str(data["id"]).strip(),
            purpose=str(data.get("purpose") or "").strip(),
            keyframe_prompt=str(data["keyframe_prompt"]).strip(),
            motion_prompt=str(data["motion_prompt"]).strip(),
            duration_s=int(data.get("duration_s") or 5),
            cast=tuple(str(c).strip() for c in data.get("cast") or [] if str(c).strip()),
            shows_product=bool(data.get("shows_product", True)),
            spec=dict(data.get("spec") or {}),
            overlay_prompt=str(data.get("overlay_prompt") or "").strip(),
        )

    def to_dict(self) -> dict:
        return asdict(self)
