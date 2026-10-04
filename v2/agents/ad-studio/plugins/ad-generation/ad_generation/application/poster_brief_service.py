"""A text ad's brief: its copy and its design.

The writer (the agent's own model) chooses the design AS FIELDS — layout, background, where the
product sits, palette, typography, mood — and words for the headline, subline and call to action
where the user gave none. The user's copy is taken verbatim; an offer or fine print comes ONLY
from the user (PosterCopy). The checklist is checked — a gap goes back to the writer once, then
fails loudly — and only then is the prompt composed (PosterPromptComposer), with every piece of
text quoted exactly.
"""

from __future__ import annotations

import json

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.format_library import FormatLibrary
from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.application.poster_prompt_composer import PosterPromptComposer
from ad_generation.domain.creative_brief import CreativeBrief
from ad_generation.domain.creative_direction import CreativeDirection
from ad_generation.domain.poster_checklist import DESIGN, PosterChecklist
from ad_generation.domain.poster_copy import PosterCopy
from ad_generation.domain.recipe import Recipe


class PosterBriefService:
    def __init__(
        self,
        reasoner: Reasoner,
        store: CampaignStore,
        formats: FormatLibrary,
        composer: PosterPromptComposer,
        instructions: str,
    ) -> None:
        self._reasoner = reasoner
        self._store = store
        self._formats = formats
        self._composer = composer
        self._instructions = instructions

    def write_for_recipe(
        self, campaign_id: str, recipe: Recipe, cast_name: str, direction: CreativeDirection
    ) -> CreativeBrief:
        if cast_name:
            raise ValueError(f"recipe {recipe.key} is a text ad with no cast member; start it without one")
        profile = self._store.profile(campaign_id)
        given = direction.copy()
        facts = {
            "product": profile.to_dict(),
            "formats": [
                {"key": f.key, "title": f.title, "guide": f.guide} for f in (self._formats.get(k) for k in recipe.formats)
            ],
            "shots": [t.to_dict() for t in recipe.shots],
            "aspect_ratio": recipe.aspect_ratio,
            "copy_given": {k: v for k, v in given.to_dict().items() if v},
            "direction": {k: v for k, v in direction.given().items() if k not in given.to_dict()},
        }
        prompt = self._instructions + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False)
        answer = self._reasoner.think(prompt)
        copy, missing = self._check(answer, given, recipe)
        if missing:
            answer = self._reasoner.think(
                prompt
                + "\n\nYOUR LAST ANSWER LEFT THESE CHECKLIST ITEMS EMPTY: "
                + ", ".join(missing)
                + ". Answer again, complete, with every one of them filled."
            )
            copy, missing = self._check(answer, given, recipe)
            if missing:
                raise ValueError("the text ad's brief still leaves checklist items empty: " + ", ".join(missing))
        specs = self._specs(answer)
        composed = {}
        for t in recipe.shots:
            keyframe, motion, overlay = self._composer.compose(
                specs[t.id], copy, profile, recipe.aspect_ratio, str(answer.get("format_key") or "")
            )
            composed[t.id] = {
                "keyframe_prompt": keyframe, "motion_prompt": motion, "overlay_prompt": overlay, "spec": specs[t.id],
            }
        brief = recipe.build_brief(answer, {}, composed, "", copy.to_dict())
        self._store.save_brief(campaign_id, brief)
        return brief

    def _check(self, answer: dict, given: PosterCopy, recipe: Recipe) -> tuple[PosterCopy, list[str]]:
        copy = given.merged(dict(answer.get("copy") or {}))
        specs = self._specs(answer)
        missing = copy.missing()
        for t in recipe.shots:
            missing += PosterChecklist.missing(t.id, specs.get(t.id) or {})
        return copy, missing

    @staticmethod
    def _specs(answer: dict) -> dict[str, dict]:
        return {
            str(s["id"]): {k: str(s.get(k) or "").strip() for k in DESIGN}
            for s in answer.get("shots") or []
            if isinstance(s, dict) and s.get("id")
        }
