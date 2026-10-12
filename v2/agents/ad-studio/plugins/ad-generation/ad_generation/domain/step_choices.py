"""The choices a step needs and the ones made — read, checked and stored where each one lives.

Pure: it reads and changes a campaign's checklist in memory; loading and saving are the caller's.
A choice lives in its setting's home (TemplateSetting): the cast member, the creative direction,
or the step's own count, model, length and resolution. Direction choices are also kept one by one
(`checklist.choices`), so several settings can fill one field ("drape" and "framing" both become
the pose) and each still shows its own value.
"""

from __future__ import annotations

from typing import Callable

from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.recipe import Recipe
from ad_generation.domain.step_run import StepRun
from ad_generation.domain.template_setting import TemplateSetting

# A run's own values, which count as choices for that run: home -> the run's value.
RunValues = dict[str, str]
# What a chosen value becomes in the creative direction (a location's name -> the place described).
Words = Callable[[TemplateSetting, str], str]


def _as_is(setting: TemplateSetting, value: str) -> str:
    return value


class StepChoices:
    @staticmethod
    def value(checklist: CampaignChecklist, step: CampaignStep, setting: TemplateSetting) -> str:
        if setting.home == "cast":
            return checklist.cast_name
        if setting.home == "direction":
            return str(checklist.choices.get(setting.key) or "")
        if setting.home == "count":
            return str(step.count or "")
        if setting.home == "model":
            return step.model
        if setting.home == "seconds":
            return str(step.seconds or "")
        return step.resolution

    @staticmethod
    def missing(recipe: Recipe, checklist: CampaignChecklist, step: CampaignStep, run: RunValues | None = None) -> list[TemplateSetting]:
        """The required settings nobody chose — neither on the campaign nor in this run."""
        run = run or {}
        return [
            s for s in recipe.settings_for(step)
            if s.required and not (str(run.get(s.home) or "").strip() or StepChoices.value(checklist, step, s))
        ]

    @staticmethod
    def require(recipe: Recipe, checklist: CampaignChecklist, step: CampaignStep, run: StepRun) -> None:
        """A run of a step with Auto off needs every required choice made — on the campaign or in
        the run itself. -> ValueError naming the unmade ones."""
        if checklist.auto_for(step):
            return
        values = {"count": str(run.count or ""), "model": run.model, "seconds": str(run.seconds or ""), "resolution": run.resolution}
        unmade = StepChoices.missing(recipe, checklist, step, values)
        if unmade:
            raise ValueError(
                f"step {step.id} needs your choice first: " + ", ".join(s.label for s in unmade)
                + " (choose them in the studio, or switch Auto on to let the agent pick)"
            )

    @staticmethod
    def apply(recipe: Recipe, checklist: CampaignChecklist, step_id: str, values: dict, words: Words = _as_is) -> CampaignStep:
        """Store the choices for a step (key -> value; "" clears one). The choice keeps the value as
        picked; the direction gets `words` for it. -> the step as it is now."""
        step = checklist.step(step_id)
        declared = {s.key: s for s in recipe.settings_for(step)}
        unknown = [k for k in values if k not in declared]
        if unknown:
            raise ValueError(f"step {step_id} has no setting {', '.join(unknown)} (it has: {', '.join(declared) or 'none'})")
        for key, raw in values.items():
            s = declared[key]
            value = s.check(raw)
            if s.home == "cast":
                checklist.cast_name = value
            elif s.home == "direction":
                if value:
                    checklist.choices[key] = value
                else:
                    checklist.choices.pop(key, None)
            elif s.home == "count":
                step = step.with_(count=int(value or 0))
            elif s.home == "model":
                step = step.with_(model=value)
            elif s.home == "seconds":
                step = step.with_(seconds=int(value or 0))
            else:
                step = step.with_(resolution=value)
        checklist.put(step)
        StepChoices._compose_direction(recipe, checklist, step, {declared[k].field for k in values if declared[k].home == "direction"}, words)
        return step

    @staticmethod
    def seed(recipe: Recipe, checklist: CampaignChecklist, given: dict) -> None:
        """At the start: what the user said in their words (location, pose…) counts as the brief's
        choice of the setting that fills that field — when exactly one setting fills it."""
        brief = checklist.first("brief")
        if brief is None:
            return
        settings = [s for s in recipe.settings_for(brief) if s.home == "direction"]
        for field, value in given.items():
            fillers = [s for s in settings if s.field == field]
            if len(fillers) == 1 and str(value).strip():
                checklist.choices.setdefault(fillers[0].key, str(value).strip())

    @staticmethod
    def view(recipe: Recipe, checklist: CampaignChecklist, step: CampaignStep) -> list[dict]:
        """Each setting with its value, and whether a required one is still unchosen."""
        out = []
        for s in recipe.settings_for(step):
            value = StepChoices.value(checklist, step, s)
            out.append({**s.to_dict(), "value": value, "missing": s.required and not value})
        return out

    @staticmethod
    def _compose_direction(recipe: Recipe, checklist: CampaignChecklist, step: CampaignStep, touched: set[str], words: Words) -> None:
        """The direction fields just chosen for, rebuilt from their settings' choices in order (a field
        no setting changed keeps what the user said in their own words)."""
        fields: dict[str, list[str]] = {}
        for s in recipe.settings_for(step):
            if s.home == "direction" and s.field in touched:
                fields.setdefault(s.field, [])
                if checklist.choices.get(s.key):
                    fields[s.field].append(words(s, checklist.choices[s.key]))
        for field, parts in fields.items():
            if parts:
                checklist.direction[field] = "; ".join(parts)
            else:
                checklist.direction.pop(field, None)
