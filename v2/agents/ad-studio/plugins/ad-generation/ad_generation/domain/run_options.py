"""What THIS run of a recipe makes — chosen per run, by the user or the agent.

The recipe fixes the STRUCTURE (which shots, their framing, who appears); how much of it to
render and at what quality is a choice per run. Every field left empty falls back to the
recipe's own default, so a run with no options is the recipe as written.
"""

from __future__ import annotations

from dataclasses import dataclass

from ad_generation.domain.recipe import Recipe
from ad_generation.domain.resolved_run import ResolvedRun

_RESOLUTIONS = ("480p", "720p", "1080p")
_GATES = ("brief", "sheet", "stills", "clips")


@dataclass(frozen=True)
class RunOptions:
    resolution: str = ""  # "" = the recipe's
    shots: tuple[str, ...] | None = None  # which recipe shots to make; None = all
    animate: tuple[str, ...] | None = None  # which made shots become clips; None = the recipe's
    variants: int = 0  # stills per attempt; 0 = the recipe's
    budget_usd: float = 0.0  # 0 = the recipe's
    gates: tuple[str, ...] | None = None  # where to stop for the user; () = run through; None = the recipe's

    def resolved(self, recipe: Recipe) -> ResolvedRun:
        known = [s.id for s in recipe.shots]
        # In recipe order, whatever order they were named in.
        shots = tuple(s for s in known if s in self.shots) if self.shots is not None else tuple(known)
        unknown = [s for s in (self.shots or ()) if s not in known]
        if unknown:
            raise ValueError(f"recipe {recipe.key} has no shot(s) {', '.join(unknown)} (shots: {', '.join(known)})")
        if not shots:
            raise ValueError("no shots to make")
        animate = (
            tuple(self.animate)
            if self.animate is not None
            # The recipe's clips among the shots this run makes.
            else tuple(s.id for s in recipe.shots if s.animate and s.id in shots)
        )
        stray = [a for a in animate if a not in shots]
        if stray:
            raise ValueError(f"cannot animate shot(s) {', '.join(stray)} that this run does not make ({', '.join(shots)})")
        resolution = self.resolution or recipe.resolution
        if resolution not in _RESOLUTIONS:
            raise ValueError(f"resolution '{resolution}' is not one of {', '.join(_RESOLUTIONS)}")
        gates = tuple(self.gates) if self.gates is not None else tuple(recipe.gates)
        unknown_gates = [g for g in gates if g not in _GATES]
        if unknown_gates:
            raise ValueError(f"unknown gate(s) {', '.join(unknown_gates)} (gates: {', '.join(_GATES)})")
        return ResolvedRun(
            shots=shots,
            animate=frozenset(animate),
            resolution=resolution,
            variants=self.variants or recipe.variants,
            budget_usd=self.budget_usd or recipe.budget_usd,
            gates=gates,
        )
