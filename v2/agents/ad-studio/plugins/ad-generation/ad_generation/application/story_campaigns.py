"""An approved story, made: one campaign per product it shows — each read from that product's own
photos, so its brief, prompts and accuracy checks are about the right product — holding that
product's shots. Shots of no product, or of all of them, go with the first (hero) product. Every
campaign's picked results go into the story's one collection (StoryCollector).

The campaigns start like any other (CampaignStepService.start), on the template the agent chose for
how the shots get made; the story decides what they are.
"""

from __future__ import annotations

from typing import Callable

from ad_generation.application.interfaces.shop_product_store import ShopProductStore
from ad_generation.application.interfaces.story_store import StoryStore
from ad_generation.domain.story import Story


class StoryCampaigns:
    def __init__(self, stories: StoryStore, products: ShopProductStore) -> None:
        self._stories = stories
        self._products = products

    def plan(self, story: Story, template: str, per_product: dict | None = None) -> list[dict]:
        """Per campaign: the product, its photos, its shots, and the other products' main photos
        (for an "all" shot). The hero product first. `per_product`: a template for a product whose
        shots are made differently (the jhumka on jewellery-model, the saree on ethnic-wear)."""
        if story.status != "approved":
            raise ValueError(f"story {story.id} is not approved (v{story.version} is a draft) — the user approves it first")
        if not story.products:
            raise ValueError(f"story {story.id} has no products")
        hero = story.products[0]
        groups: list[dict] = []
        for key in story.products:
            product = self._products.get(key)
            shots = [s.id for s in story.shots if s.product == key or (key == hero and s.product in ("", "all"))]
            if not shots:
                continue
            others = tuple(self._products.get(k).photos[0] for k in story.products if k != key and self._products.get(k).photos)
            groups.append({
                "product": key, "name": product.title, "photos": list(product.photos + product.angles)[:4],
                "plan": {"template": (per_product or {}).get(key) or template, "story": story.to_dict(), "shots": shots, "others": list(others)},
            })
        return groups

    def make(
        self, story_id: str, template: str, start: Callable[[str, list[str], dict], str], per_product: dict | None = None
    ) -> list[str]:
        """Start each campaign with `start(name, photos, plan)` -> its id. -> the campaign ids."""
        story = self._stories.get(story_id)
        return [start(g["name"], g["photos"], g["plan"]) for g in self.plan(story, template, per_product)]
