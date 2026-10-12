"""The recipe library every campaign reads, with story campaigns in it: a key `story:<campaign>` is
that campaign's story-shaped recipe (StoryRecipe), built from the story as it was when the campaign
was made (its saved plan) on top of the template it uses. Every other key is the template itself."""

from __future__ import annotations

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.recipe_library import RecipeLibrary
from ad_generation.domain.recipe import Recipe
from ad_generation.domain.story import Story
from ad_generation.domain.story_recipe import StoryRecipe


class StoryRecipeLibrary:
    def __init__(self, templates: RecipeLibrary, campaigns: CampaignStore) -> None:
        self._templates = templates
        self._campaigns = campaigns

    def all(self) -> list[Recipe]:
        return self._templates.all()

    def get(self, key: str) -> Recipe:
        campaign_id = StoryRecipe.campaign_of(key)
        if not campaign_id:
            return self._templates.get(key)
        plan = self._campaigns.story_plan(campaign_id)
        return StoryRecipe.build(
            self._templates.get(str(plan["template"])), Story.from_dict(plan["story"]),
            tuple(str(s) for s in plan["shots"]), tuple(str(p) for p in plan.get("others") or ()), key,
        )
