"""A run's options with the recipe's defaults filled in — what the run actually does."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedRun:
    shots: tuple[str, ...]  # the recipe shots this run makes, in recipe order
    animate: frozenset[str]  # which of them become clips
    resolution: str
    variants: int
    budget_usd: float
    gates: tuple[str, ...]  # where this run stops for the user; () runs straight through

    def to_dict(self) -> dict:
        return {
            "shots": list(self.shots),
            "animate": sorted(self.animate),
            "resolution": self.resolution,
            "variants": self.variants,
            "budget_usd": self.budget_usd,
            "gates": list(self.gates),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ResolvedRun":
        if "gates" not in data:
            # Absent is not "none": a plan saved without the field would otherwise run straight
            # through every gate.
            raise ValueError("this campaign's plan names no gates (it was started before gates existed); start it again")
        return cls(
            shots=tuple(data["shots"]),
            animate=frozenset(data["animate"]),
            resolution=str(data["resolution"]),
            variants=int(data["variants"]),
            budget_usd=float(data["budget_usd"]),
            gates=tuple(data["gates"]),
        )
