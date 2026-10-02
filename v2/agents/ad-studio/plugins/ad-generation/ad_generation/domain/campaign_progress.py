"""Where a campaign is in its recipe — which gate it waits at, and what each shot has so far.

THE GATES. A campaign runs one step, then STOPS at a gate for the user to look and decide:
    brief  — the look and each shot's composed prompts
    sheet  — the shoot sheet (recipes with shoot_sheet): the cast member in the ad's outfit and light
    stills — each shot's checked stills; the user picks one per shot or asks for a redo
    clips  — each clip; the user approves or asks for a redo
A recipe lists which gates it stops at; a step whose gate is not listed runs straight on.
`gate_reached_at` is when the gate's results were made: only a user answer AFTER it may move
the campaign on (see ApprovalLedger).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ad_generation.domain.resolved_run import ResolvedRun
from ad_generation.domain.shot_outcome import ShotOutcome

BRIEF, SHEET, STILLS, CLIPS, DONE = "brief", "sheet", "stills", "clips", "done"
ORDER = (BRIEF, SHEET, STILLS, CLIPS, DONE)


@dataclass
class CampaignProgress:
    recipe_key: str
    plan: ResolvedRun
    direction: dict  # the CreativeDirection given, as fields
    cast_name: str
    gate: str  # the gate the campaign waits at
    gate_reached_at: float
    outcomes: dict[str, ShotOutcome] = field(default_factory=dict)
    sheet: str = ""  # the shoot sheet's path, when the recipe makes one
    sheet_score: int = 0
    session: str = ""  # the chat that started it — how a window finds its own campaign
    video: str = ""  # "provider/model" the user chose for the clips; "" = the configured default

    def to_dict(self) -> dict:
        return {
            "recipe_key": self.recipe_key,
            "plan": self.plan.to_dict(),
            "direction": self.direction,
            "cast_name": self.cast_name,
            "gate": self.gate,
            "gate_reached_at": self.gate_reached_at,
            "outcomes": {k: v.to_dict() for k, v in self.outcomes.items()},
            "sheet": self.sheet,
            "sheet_score": self.sheet_score,
            "session": self.session,
            "video": self.video,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CampaignProgress":
        return cls(
            recipe_key=str(data["recipe_key"]),
            plan=ResolvedRun.from_dict(data["plan"]),
            direction=dict(data.get("direction") or {}),
            cast_name=str(data.get("cast_name") or ""),
            gate=str(data["gate"]),
            gate_reached_at=float(data["gate_reached_at"]),
            outcomes={k: ShotOutcome.from_dict(v) for k, v in (data.get("outcomes") or {}).items()},
            sheet=str(data.get("sheet") or ""),
            sheet_score=int(data.get("sheet_score") or 0),
            session=str(data.get("session") or ""),
            video=str(data.get("video") or ""),
        )
