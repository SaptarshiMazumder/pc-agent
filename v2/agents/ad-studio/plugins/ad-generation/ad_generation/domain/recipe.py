"""A recipe: the fixed way an ad is made for a kind of product.

A handbag and a purse run the same recipe, shot for shot — nothing is rediscovered per product.
What varies is the product, the cast member and the scene the brief writes into each fixed shot.
"""

from __future__ import annotations

from dataclasses import dataclass

from ad_generation.domain.creative_brief import CreativeBrief
from ad_generation.domain.shot import Shot
from ad_generation.domain.shot_template import ShotTemplate


@dataclass(frozen=True)
class Recipe:
    key: str
    title: str
    covers: str  # the kinds of product it is for, in words — what the product reader matches
    formats: tuple[str, ...]  # the playbooks a scene may follow; the brief picks the best fit
    aspect_ratio: str
    shots: tuple[ShotTemplate, ...]
    variants: int  # stills per attempt
    max_still_attempts: int
    max_clip_attempts: int
    resolution: str
    budget_usd: float  # a campaign stops before spending past this
    # Where the campaign stops for the user to approve: any of "brief", "stills", "clips".
    gates: tuple[str, ...] = ("brief", "stills", "clips")
    # Make a shoot sheet (the cast member in the brief's wardrobe and light, six views) before
    # the stills, and reference it from every still and clip.
    shoot_sheet: bool = False

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "Recipe":
        shots = tuple(ShotTemplate.from_dict(s) for s in data.get("shots") or [])
        if not shots:
            raise ValueError(f"recipe {key} has no shots")
        if not data.get("formats"):
            raise ValueError(f"recipe {key} names no formats")
        gates = tuple(str(g) for g in data.get("gates", ("brief", "stills", "clips")))
        unknown = [g for g in gates if g not in ("brief", "sheet", "stills", "clips")]
        if unknown:
            raise ValueError(f"recipe {key} names unknown gate(s): {', '.join(unknown)}")
        return cls(
            key=key,
            title=str(data.get("title") or key),
            covers=str(data.get("covers") or ""),
            formats=tuple(str(f) for f in data["formats"]),
            aspect_ratio=str(data.get("aspect_ratio") or "9:16"),
            shots=shots,
            variants=int(data.get("variants", 2)),
            max_still_attempts=int(data.get("max_still_attempts", 3)),
            max_clip_attempts=int(data.get("max_clip_attempts", 2)),
            resolution=str(data.get("resolution") or "720p"),
            budget_usd=float(data.get("budget_usd", 15)),
            gates=gates,
            shoot_sheet=bool(data.get("shoot_sheet", False)),
        )

    def needs_cast(self) -> bool:
        return any(s.cast for s in self.shots)

    def template(self, shot_id: str) -> ShotTemplate:
        for s in self.shots:
            if s.id == shot_id:
                return s
        raise KeyError(f"recipe {self.key} has no shot '{shot_id}'")

    def build_brief(self, header: dict, look: dict, composed: dict, cast_member: str) -> CreativeBrief:
        """The brief, with the recipe's structure enforced: the writer supplies only the scene of
        each fixed shot (composed into `composed[id]` = keyframe_prompt, motion_prompt, spec),
        the look, and the format, concept, hook and caption. Shot ids, purposes, casting and
        durations come from the recipe, whatever the writer answered."""
        if header.get("format_key") not in self.formats:
            raise ValueError(
                f"the brief chose format '{header.get('format_key')}', not one of {', '.join(self.formats)}"
            )
        missing = [t.id for t in self.shots if t.id not in composed]
        if missing:
            raise ValueError("the brief left out the scene for shot(s): " + ", ".join(missing))
        shots = tuple(
            Shot.from_dict(
                {
                    "id": t.id,
                    "purpose": t.purpose,
                    "keyframe_prompt": composed[t.id]["keyframe_prompt"],
                    "motion_prompt": composed[t.id]["motion_prompt"],
                    "duration_s": t.duration_s,
                    "cast": [cast_member] if t.cast else [],
                    "shows_product": t.shows_product,
                    "spec": composed[t.id]["spec"],
                }
            )
            for t in self.shots
        )
        return CreativeBrief(
            format_key=str(header["format_key"]),
            concept=str(header.get("concept") or ""),
            hook=str(header.get("hook") or ""),
            aspect_ratio=self.aspect_ratio,
            shots=shots,
            caption=str(header.get("caption") or ""),
            look=dict(look),
        )
