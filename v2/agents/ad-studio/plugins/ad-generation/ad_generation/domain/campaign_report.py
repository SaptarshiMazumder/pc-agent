"""What a campaign step produced: the gate it stopped at, the brief, each shot, the cost."""

from __future__ import annotations

from dataclasses import dataclass

from ad_generation.domain.creative_brief import CreativeBrief
from ad_generation.domain.shot_outcome import ShotOutcome


@dataclass(frozen=True)
class CampaignReport:
    campaign_id: str
    recipe_key: str
    gate: str  # the gate the campaign now waits at ("done" when finished)
    brief: CreativeBrief
    planned: tuple[str, ...]  # the shots this run makes, of the brief's
    shots: tuple[ShotOutcome, ...]
    sheet: str  # the shoot sheet, when the recipe makes one
    sheet_score: int
    spent_usd: float
    stopped: str  # why a step stopped early ("budget reached: ..."), or ""

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "recipe_key": self.recipe_key,
            "gate": self.gate,
            "brief": self.brief.to_dict(),
            "planned": list(self.planned),
            "shots": [s.to_dict() for s in self.shots],
            "sheet": self.sheet,
            "sheet_score": self.sheet_score,
            "spent_usd": self.spent_usd,
            "stopped": self.stopped,
        }
