"""Product photos -> a campaign with the product's profile: what it is, what must stay exact, and
which recipe this kind of product is made with."""

from __future__ import annotations

import json

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.application.interfaces.recipe_library import RecipeLibrary
from ad_generation.domain.product_profile import ProductProfile


class ProductAnalysisService:
    def __init__(self, reasoner: Reasoner, store: CampaignStore, recipes: RecipeLibrary, instructions: str) -> None:
        self._reasoner = reasoner
        self._store = store
        self._recipes = recipes
        self._instructions = instructions

    def analyze(self, name: str, photos: list[str], notes: str) -> tuple[str, ProductProfile]:
        if not photos:
            raise ValueError("give at least one product photo")
        recipes = self._recipes.all()
        campaign_id, copies = self._store.create(name, photos)
        facts = {
            "name": name,
            "notes": notes,
            "recipes": [{"key": r.key, "covers": r.covers} for r in recipes],
        }
        prompt = self._instructions + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False)
        profile = ProductProfile.from_dict(self._reasoner.read_images(prompt, list(copies)), copies)
        if profile.recipe not in {r.key for r in recipes}:
            raise ValueError(
                f"the product reader matched no recipe ('{profile.recipe}') for a {profile.category}; "
                "recipes: " + ", ".join(r.key for r in recipes)
            )
        self._store.save_profile(campaign_id, profile)
        return campaign_id, profile
