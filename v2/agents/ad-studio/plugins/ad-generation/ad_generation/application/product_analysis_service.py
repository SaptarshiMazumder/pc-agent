"""Product photos -> a campaign with the product's profile: what it is, what must stay exact, and
which recipe this kind of product is made with.

When the product is one of the shop's categories (a saree, a necklace…), the reader turns that
category's checks into the product's own must-keep details, and the category decides the template
and how it is framed."""

from __future__ import annotations

import json
from dataclasses import replace

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.category_library import CategoryLibrary
from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.application.interfaces.recipe_library import RecipeLibrary
from ad_generation.domain.product_profile import ProductProfile


class ProductAnalysisService:
    def __init__(
        self, reasoner: Reasoner, store: CampaignStore, recipes: RecipeLibrary, categories: CategoryLibrary, instructions: str
    ) -> None:
        self._reasoner = reasoner
        self._store = store
        self._recipes = recipes
        self._categories = categories
        self._instructions = instructions

    def analyze(self, name: str, photos: list[str], notes: str) -> tuple[str, ProductProfile]:
        if not photos:
            raise ValueError("give at least one product photo")
        # Only recipes a product can be matched to — one the user must choose (product stills) is not.
        recipes = [r for r in self._recipes.all() if r.auto]
        campaign_id, copies = self._store.create(name, photos)
        facts = {
            "name": name,
            "notes": notes,
            "recipes": [{"key": r.key, "covers": r.covers} for r in recipes],
            "categories": [{"key": c.key, "covers": c.covers, "checks": list(c.checks)} for c in self._categories.all()],
        }
        prompt = self._instructions + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False)
        profile = ProductProfile.from_dict(self._reasoner.read_images(prompt, list(copies)), copies)
        if profile.category_key:
            category = self._categories.get(profile.category_key)
            profile = replace(profile, recipe=category.template, shot_guidance=category.shot)
        if profile.recipe not in {r.key for r in recipes}:
            raise ValueError(
                f"the product reader matched no recipe ('{profile.recipe}') for a {profile.category}; "
                "recipes: " + ", ".join(r.key for r in recipes)
            )
        self._store.save_profile(campaign_id, profile)
        return campaign_id, profile
