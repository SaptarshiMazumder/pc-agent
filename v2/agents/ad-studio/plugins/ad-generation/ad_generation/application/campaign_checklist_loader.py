"""A campaign's checklist, wherever it comes from: its own steps.json, or — a campaign from before
checklists — its old progress read through its recipe, and saved as a checklist from then on."""

from __future__ import annotations

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.recipe_library import RecipeLibrary
from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.legacy_progress import checklist_from_progress


class CampaignChecklistLoader:
    def __init__(self, store: CampaignStore, recipes: RecipeLibrary) -> None:
        self._store = store
        self._recipes = recipes

    def load(self, campaign_id: str) -> CampaignChecklist:
        checklist = self._store.checklist(campaign_id)
        if checklist is not None:
            return checklist
        legacy = self._store.legacy_progress(campaign_id)
        if legacy is None:
            raise KeyError(f"campaign '{campaign_id}' has no checklist (start it with campaign_start)")
        checklist = checklist_from_progress(legacy, self._recipes.get(str(legacy["recipe_key"])))
        self._store.save_checklist(campaign_id, checklist)
        return checklist
