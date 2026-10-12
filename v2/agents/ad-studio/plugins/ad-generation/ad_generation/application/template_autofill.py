"""Auto, for one step: the agent's picks for the REQUIRED choices nobody made — the template's
suggestion, else the first of the setting's options (a location the product's category suits, its
first pose). Used only when the step's Auto is on; with Auto off nothing is ever filled.

Who is in it and which model are not picked here: the brief casts the model, and a run without a
model uses the configured default.
"""

from __future__ import annotations

from ad_generation.application.template_setting_sources import TemplateSettingSources
from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.category_profile import CategoryProfile
from ad_generation.domain.recipe import Recipe
from ad_generation.domain.step_choices import StepChoices


class TemplateAutofill:
    def __init__(self, sources: TemplateSettingSources) -> None:
        self._sources = sources

    def fill(self, recipe: Recipe, checklist: CampaignChecklist, step: CampaignStep, category: CategoryProfile | None) -> dict:
        """-> key -> the value to choose, for each required setting still unchosen."""
        if not checklist.auto_for(step):
            raise ValueError(f"step {step.id} is not on Auto: its choices are the user's")
        picks = {}
        for s in StepChoices.missing(recipe, checklist, step):
            if s.home in ("cast", "model"):
                continue
            pick = s.suggested or next(iter(self._sources.options(s, category)), "")
            if pick:
                picks[s.key] = pick
        return picks
