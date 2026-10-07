"""FixedRecipeCheck — a TESTED recipe is filled in, never edited.

Some recipes are a workflow proven on Comfy Cloud exactly as it is: the person-from-reference
still and the head swap. Left open, the agent "improved" them every time — a LoRA added to the
still (the body came out slim, twice), the head-swap prompt reworded, another seed (the swap came
out as a crunched, unswapped picture, three times out of three; the tested seed never did).

A recipe marks itself `"fixed": {"ports": [...], "why": "..."}`: the ports named there are the
job's to set (the shot, the size), every other port keeps the tested value, and no LoRA or model
swap goes on it. Anything else is a PROBLEM — the design does not hold — with the reason, so the
agent fills the template in instead of rewriting it.
"""

from __future__ import annotations

from pipeline import Stage
from recipe import Recipe


class FixedRecipeCheck:
    def check(self, stage: Stage, recipe: Recipe | None) -> list[str]:
        """Problems: what the stage changes on a fixed recipe beyond its open ports."""
        fixed = (recipe.meta.get("fixed") if recipe is not None else None) or None
        if not isinstance(fixed, dict):
            return []
        open_ports = set(fixed.get("ports") or [])
        why = str(fixed.get("why") or "it is a workflow tested on Comfy Cloud exactly as it is")
        problems: list[str] = []
        changed = sorted(p for p, v in stage.ports.items() if p not in open_ports and v != self._tested(recipe, p))
        if changed:
            problems.append(
                f"stage {stage.name}: {recipe.family}/{recipe.id} is a FIXED recipe — {why}. Only "
                f"{', '.join(sorted(open_ports)) or 'its inputs'} may be set; put {', '.join(changed)} back "
                f"with stage_set ports {{{', '.join(repr(p) + ': null' for p in changed)}}} so the tested "
                "values run.")
        if stage.loras:
            problems.append(f"stage {stage.name}: {recipe.family}/{recipe.id} is a FIXED recipe — {why}. It takes no "
                            f"LoRA ({', '.join(lo.name for lo in stage.loras)}): stage_set it with loras [].")
        if stage.models:
            problems.append(f"stage {stage.name}: {recipe.family}/{recipe.id} is a FIXED recipe — {why}. Its models "
                            "are not swapped: stage_set it with models [].")
        return problems

    @staticmethod
    def _tested(recipe: Recipe, port: str):
        """The value the recipe's own graph holds for `port` (what the tested run used)."""
        spec = recipe.ports.get(port) or {}
        node = spec.get("node") or (spec.get("nodes") or [None])[0]
        return ((recipe.graph.get(str(node)) or {}).get("inputs") or {}).get(spec.get("input")) if node else None


__all__ = ["FixedRecipeCheck"]
