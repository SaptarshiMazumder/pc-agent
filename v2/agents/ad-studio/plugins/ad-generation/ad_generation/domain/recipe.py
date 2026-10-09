"""A recipe: the default way an ad is made for a kind of product — DATA, a JSON file.

Two things, both plain lists:
  scenes (`shots`)  what the brief writes a prompt for: framing, who appears, the product shown
  steps             the default checklist: which actions, in which order

A campaign starts with a copy of the steps and owns it from then on — the user adds, skips and
re-runs steps freely (CampaignChecklist). Nothing here runs anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.creative_brief import CreativeBrief
from ad_generation.domain.shot import Shot
from ad_generation.domain.shot_template import ShotTemplate


@dataclass(frozen=True)
class Recipe:
    key: str
    name: str  # the short name the window shows ("Wearables + shoot sheet")
    title: str
    covers: str  # the kinds of product it is for, in words — what the product reader matches
    formats: tuple[str, ...]  # the playbooks a scene may follow; the brief picks the best fit
    aspect_ratio: str
    shots: tuple[ShotTemplate, ...]  # the scenes the brief writes
    steps: tuple[CampaignStep, ...]  # the default checklist
    variants: int  # images per run of an images step (3 unless the recipe says)
    resolution: str  # clips' default resolution
    budget_usd: float  # a campaign stops before spending past this (the user can raise it)
    # Matched to a product by the product reader; False = only when the user chooses it.
    auto: bool = True
    # Scenes the window offers when the recipe is chosen ("In an open gift box, from above").
    scene_options: tuple[str, ...] = ()
    # What its brief writes: "scene" (a photo ad's look and shots) or "poster" (a text ad's copy
    # and design).
    brief: str = "scene"
    # Added to the window's start sentence — blanks for what the user should say (a text ad's copy).
    start_text: str = ""

    @classmethod
    def from_dict(cls, key: str, data: dict) -> "Recipe":
        shots = tuple(ShotTemplate.from_dict(s) for s in data.get("shots") or [])
        if not shots:
            raise ValueError(f"recipe {key} has no scenes (shots)")
        if not data.get("formats"):
            raise ValueError(f"recipe {key} names no formats")
        steps = tuple(CampaignStep.from_dict(s) for s in data.get("steps") or [])
        if not steps:
            raise ValueError(f"recipe {key} has no steps")
        scenes = {s.id for s in shots}
        stray = [s.id for s in steps if s.scene and s.scene not in scenes]
        if stray:
            raise ValueError(f"recipe {key}: step(s) {', '.join(stray)} name a scene it does not have")
        brief = str(data.get("brief") or "scene")
        if brief not in ("scene", "poster"):
            raise ValueError(f"recipe {key}: brief is 'scene' or 'poster', not '{brief}'")
        if not data.get("name"):
            raise ValueError(f"recipe {key} has no name")
        return cls(
            key=key,
            name=str(data["name"]),
            title=str(data.get("title") or key),
            covers=str(data.get("covers") or ""),
            formats=tuple(str(f) for f in data["formats"]),
            aspect_ratio=str(data.get("aspect_ratio") or "9:16"),
            shots=shots,
            steps=steps,
            variants=int(data.get("variants", 3)),
            resolution=str(data.get("resolution") or "720p"),
            budget_usd=float(data.get("budget_usd", 15)),
            auto=bool(data.get("auto", True)),
            scene_options=tuple(str(o) for o in data.get("scene_options") or ()),
            brief=brief,
            start_text=str(data.get("start_text") or ""),
        )

    def needs_cast(self) -> bool:
        return any(s.cast for s in self.shots)

    def new_checklist(self, cast_name: str, direction: dict, session: str, approval: str = "ask") -> CampaignChecklist:
        """A campaign's own copy of the steps, each image step making the recipe's count."""
        return CampaignChecklist(
            recipe_key=self.key,
            cast_name=cast_name,
            direction=dict(direction),
            session=session,
            budget_usd=self.budget_usd,
            steps=[self._defaulted(s) for s in self.steps],
            approval=approval,
        )

    def _defaulted(self, step: CampaignStep) -> CampaignStep:
        """The recipe's defaults filled into a step: the image count, the clip resolution."""
        if step.action == "images":
            return step.with_(count=step.count or self.variants)
        if step.action == "video":
            return step.with_(resolution=step.resolution or self.resolution)
        return step

    def template(self, shot_id: str) -> ShotTemplate:
        for s in self.shots:
            if s.id == shot_id:
                return s
        raise KeyError(f"recipe {self.key} has no scene '{shot_id}'")

    def build_brief(
        self, header: dict, look: dict, composed: dict, cast_member: str, copy: dict | None = None
    ) -> CreativeBrief:
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
                    "overlay_prompt": composed[t.id].get("overlay_prompt", ""),
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
            copy=dict(copy or {}),
        )
