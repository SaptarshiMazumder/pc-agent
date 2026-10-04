"""LoraCompatibility — is each LoRA of a stage trained for the model that stage runs?

ComfyUI never refuses a LoRA made for another model: the keys it cannot place are logged ("lora key
not loaded") and the render comes out as if there were no LoRA. So the check is ours:

  * a LoRA whose `base` (Civitai's word for what it was trained on) is not one the stage's model
    takes is a PROBLEM — "Flux.1 D" on Z-Image Turbo, a Klein 9B LoRA on FLUX.2 dev;
  * without a `base`, the file name speaks where it can: Comfy Cloud names its LoRAs by model
    (`zimage-…`, `flux1-lora-…`, `flux-2-klein-9b-…`), and each model of a family's profile lists
    those patterns (`cloud`). A name that belongs to another model is a PROBLEM; a name nothing
    recognises is a QUESTION;
  * a trigger word the prompt does not carry is a QUESTION — the effect is weak without it.
"""

from __future__ import annotations

import re

from knowledge_base_catalog import KnowledgeBaseCatalog
from pipeline import Stage
from stage_lora_splicer import StageLoraSplicer


class LoraCompatibility:
    def __init__(self, catalog: KnowledgeBaseCatalog) -> None:
        self._catalog = catalog

    def check(self, stage: Stage, graph: dict, prompt: str = "") -> tuple[list[str], list[str]]:
        """(problems, questions) for the stage's LoRAs against its built graph."""
        problems: list[str] = []
        questions: list[str] = []
        if not stage.loras or stage.custom:
            return problems, questions
        fam = self._catalog.families.get(stage.family)
        targets = StageLoraSplicer.targets(fam.lora if fam else {}, graph)
        for lo in stage.loras:
            fits = [t for t in targets if not lo.expert or t["expert"] == lo.expert]
            allowed = sorted({b for t in fits for b in t["bases"]})
            model = ", ".join(t["file"] for t in fits) or stage.family
            if lo.base:
                if allowed and lo.base not in self._civitai_bases():
                    problems.append(f"stage {stage.name}: LoRA {lo.name} has base {lo.base!r}, which is not Civitai's "
                                    f"base-model tag for any model here — `base` is Civitai's tag for what the LoRA "
                                    f"was trained on, as lora_search shows it. "
                                    f"This stage takes {' / '.join(repr(b) for b in allowed)}: give that, or leave "
                                    "`base` out for a Comfy Cloud LoRA (its name says the model).")
                elif allowed and lo.base not in allowed:
                    problems.append(f"stage {stage.name}: LoRA {lo.name} was trained for {lo.base}, but this stage "
                                    f"runs {model}, which takes {' / '.join(allowed)} LoRAs — ComfyUI would apply it "
                                    "silently and the style would not appear. Pick a LoRA for this model "
                                    "(lora_search with this family), or a recipe of the LoRA's model.")
            elif not self._named_for(lo.name, fits):
                owners = self._owners(lo.name)
                if owners:
                    problems.append(f"stage {stage.name}: LoRA {lo.name} is named like a "
                                    f"{' / '.join(sorted(owners))} LoRA, but this stage runs {model} "
                                    "— ComfyUI would apply it silently with no effect.")
                else:
                    questions.append(f"stage {stage.name}: nothing says which model LoRA {lo.name} was trained for. "
                                     f"Give its `base` (lora_search shows it) — this stage takes "
                                     f"{' / '.join(allowed) or stage.family} LoRAs.")
            if lo.trigger and prompt and lo.trigger.lower() not in prompt.lower():
                questions.append(f"stage {stage.name}: the prompt does not carry LoRA {lo.name}'s trigger "
                                 f"{lo.trigger!r} — the effect is weak without it.")
        return problems, questions

    def _civitai_bases(self) -> set[str]:
        """Every Civitai base-model tag the knowledge base knows (checked against /api/v1/enums)."""
        return {b for fam in self._catalog.families.values() for m in fam.lora.get("models") or []
                for b in m.get("bases") or []}

    @staticmethod
    def _named_for(name: str, fits: list[dict]) -> bool:
        """Does `name` follow the Comfy Cloud naming of the models the LoRA would go on?"""
        return any(re.search(p, _base(name), re.I) for t in fits for p in t["cloud"])

    def _owners(self, name: str) -> set[str]:
        """The models whose Comfy Cloud LoRA naming `name` follows."""
        return {str(model.get('name') or fam.id) for fam in self._catalog.families.values()
                for model in (fam.lora.get("models") or [])
                if any(re.search(p, _base(name), re.I) for p in model.get("cloud") or [])}


def _base(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


__all__ = ["LoraCompatibility"]
