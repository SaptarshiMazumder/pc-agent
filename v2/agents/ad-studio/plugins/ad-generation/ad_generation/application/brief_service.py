"""The ad's brief — the PROMPT GENERATION step.

The writer (the agent's own model) makes the creative choices AS FIELDS: one look for the whole
ad (wardrobe, location, time of day, weather, mood) and, per shot, pose, background, framing,
lighting, action and camera move. The user's direction is a requirement: every field they gave
is taken verbatim. The checklist is then checked — an empty item goes back to the writer once,
naming what is missing, and fails loudly if it is still missing — and only then are the prompts
COMPOSED from the fields by the PromptComposer, so no prompt ever goes out with an item left out.

It fills the recipe's scenes. A result the recipe has no scene for (a product-only tabletop
image) is a step of its own with its own prompt — it never rewrites this brief.
"""

from __future__ import annotations

import json

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.format_library import FormatLibrary
from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.application.prompt_composer import PromptComposer
from ad_generation.domain.creative_brief import CreativeBrief
from ad_generation.domain.creative_direction import CreativeDirection
from ad_generation.domain.product_profile import ProductProfile
from ad_generation.domain.recipe import Recipe
from ad_generation.domain.shot_checklist import SHOT, ShotChecklist


class BriefService:
    def __init__(
        self,
        reasoner: Reasoner,
        store: CampaignStore,
        formats: FormatLibrary,
        cast: CastLibrary,
        composer: PromptComposer,
        recipe_instructions: str,
    ) -> None:
        self._reasoner = reasoner
        self._store = store
        self._formats = formats
        self._cast = cast
        self._composer = composer
        self._recipe_instructions = recipe_instructions

    # ---- the recipe's fixed shots ------------------------------------------------------------

    def write_for_recipe(
        self, campaign_id: str, recipe: Recipe, cast_name: str, direction: CreativeDirection
    ) -> CreativeBrief:
        profile = self._store.profile(campaign_id)
        if not recipe.needs_cast():
            choices = []
        elif cast_name:
            choices = [self._cast.get(cast_name)]
        else:
            choices = self._cast.all()
            if not choices:
                raise ValueError(f"recipe {recipe.key} casts a model and there is no cast yet — create one with cast_create")
        facts = {
            "product": profile.to_dict(),
            "cast_choices": [{"name": c.name, "description": c.description} for c in choices],
            "formats": [
                {"key": f.key, "title": f.title, "guide": f.guide} for f in (self._formats.get(k) for k in recipe.formats)
            ],
            "shots": [t.to_dict() for t in recipe.shots],
            "direction": direction.given(),
        }
        answer, look = self._ask(
            self._recipe_instructions, facts, direction, recipe.needs_cast(), [t.id for t in recipe.shots]
        )
        cast_member = ""
        if choices:
            cast_member = str(answer.get("cast_member") or "")
            if cast_member not in {c.name for c in choices}:
                raise ValueError(f"the brief cast '{cast_member}', not one of: " + ", ".join(c.name for c in choices))
        specs = self._specs(answer)
        composed = {
            t.id: self._compose(look, specs[t.id], cast_member if t.cast else "", profile) for t in recipe.shots
        }
        brief = recipe.build_brief(answer, look, composed, cast_member)
        self._store.save_brief(campaign_id, brief)
        return brief

    # ---- the checklist -----------------------------------------------------------------------

    def _ask(
        self,
        instructions: str,
        facts: dict,
        direction: CreativeDirection,
        has_cast: bool | None,
        shot_ids: list[str] | None,
    ) -> tuple[dict, dict]:
        """The writer's answer and the ad's look, with every checklist item filled. One retry
        naming the gaps; a second gap is an error, never a prompt with an item left out.
        `has_cast`/`shot_ids` None = read them from the answer (a free plan)."""
        prompt = instructions + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False)
        answer = self._reasoner.think(prompt)
        missing, look = self._check(answer, direction, has_cast, shot_ids)
        if missing:
            answer = self._reasoner.think(
                prompt
                + "\n\nYOUR LAST ANSWER LEFT THESE CHECKLIST ITEMS EMPTY: "
                + ", ".join(missing)
                + ". Answer again, complete, with every one of them filled."
            )
            missing, look = self._check(answer, direction, has_cast, shot_ids)
            if missing:
                raise ValueError("the brief still leaves checklist items empty: " + ", ".join(missing))
        return answer, look

    @staticmethod
    def _check(answer: dict, direction: CreativeDirection, has_cast: bool | None, shot_ids: list[str] | None):
        look = direction.apply_to_look(dict(answer.get("look") or {}))
        written = {str(s.get("id")): s for s in answer.get("shots") or [] if isinstance(s, dict)}
        if shot_ids is None:
            shot_ids = list(written)
            if not shot_ids:
                return ["shots"], look
        if has_cast is None:
            has_cast = any(written[i].get("cast") for i in shot_ids)
        missing = ShotChecklist.missing_look(look, has_cast)
        for sid in shot_ids:
            missing += ShotChecklist.missing_shot(sid, written.get(sid) or {})
        return missing, look

    @staticmethod
    def _specs(answer: dict) -> dict[str, dict]:
        return {
            str(s["id"]): {k: str(s.get(k) or "").strip() for k in SHOT}
            for s in answer.get("shots") or []
            if isinstance(s, dict) and s.get("id")
        }

    def _compose(self, look: dict, spec: dict, cast_name: str, profile: ProductProfile) -> dict:
        keyframe, motion = self._composer.compose(look, spec, cast_name, profile)
        return {"keyframe_prompt": keyframe, "motion_prompt": motion, "spec": spec}
