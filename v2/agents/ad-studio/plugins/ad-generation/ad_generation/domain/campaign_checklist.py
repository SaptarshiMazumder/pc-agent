"""A campaign: its checklist of steps, and what holds for all of them (the cast member, the user's
direction, the budget, the chat it belongs to).

THE RECIPE IS ONLY THE DEFAULT ORDER. A campaign starts with a copy of its recipe's steps; from
then on the checklist is the campaign's own. A step is run, re-run, skipped, or added when the user
asks for something the recipe does not have — and nothing else moves: there is no gate pointer,
and no step is ever locked. `current` is only "the first step not done yet", for the window to
highlight and for "continue" to mean something.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ad_generation.domain.campaign_step import CampaignStep


@dataclass
class CampaignChecklist:
    recipe_key: str
    cast_name: str
    direction: dict  # the CreativeDirection given, as fields
    session: str  # the chat that started it — how a window finds its own campaign
    budget_usd: float
    steps: list[CampaignStep] = field(default_factory=list)
    # "ask": nothing is generated without the user's click in the studio, which approves exactly
    # that run (step, model, count, length…); "auto": the agent generates when asked.
    approval: str = "ask"
    # One-time approvals the studio recorded for runs it is about to send: [{token, step, tool, args}].
    approvals: list[dict] = field(default_factory=list)

    def step(self, step_id: str) -> CampaignStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(f"no step '{step_id}' (steps: {', '.join(s.id for s in self.steps)})")

    def put(self, step: CampaignStep) -> None:
        """Replace the step with the same id."""
        self.steps = [step if s.id == step.id else s for s in self.steps]

    def add(self, step: CampaignStep, after: str = "") -> None:
        """A step of the user's own, after `after` (default: at the end)."""
        if any(s.id == step.id for s in self.steps):
            raise ValueError(f"there is already a step '{step.id}'")
        if step.source:
            self.step(step.source)  # raises, naming the steps, when it names no step
        if not after:
            self.steps.append(step)
            return
        index = [s.id for s in self.steps].index(self.step(after).id)
        self.steps.insert(index + 1, step)

    def new_id(self, title: str) -> str:
        """A step id from its title, unique in this checklist."""
        base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:24] or "step"
        n, candidate = 1, base
        while any(s.id == candidate for s in self.steps):
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    def current(self) -> CampaignStep | None:
        return next((s for s in self.steps if s.status == "todo"), None)

    def first(self, action: str) -> CampaignStep | None:
        return next((s for s in self.steps if s.action == action), None)

    def to_dict(self) -> dict:
        return {
            "recipe_key": self.recipe_key,
            "cast_name": self.cast_name,
            "direction": self.direction,
            "session": self.session,
            "budget_usd": self.budget_usd,
            "steps": [s.to_dict() for s in self.steps],
            "approval": self.approval,
            "approvals": list(self.approvals),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CampaignChecklist":
        return cls(
            recipe_key=str(data["recipe_key"]),
            cast_name=str(data.get("cast_name") or ""),
            direction=dict(data.get("direction") or {}),
            session=str(data.get("session") or ""),
            budget_usd=float(data.get("budget_usd") or 0.0),
            steps=[CampaignStep.from_dict(s) for s in data.get("steps") or []],
            approval=str(data.get("approval") or "ask"),
            approvals=[dict(a) for a in data.get("approvals") or []],
        )
