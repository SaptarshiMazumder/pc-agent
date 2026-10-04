"""Whether a run that generates (and costs) may go ahead — the user approves the MODEL, by clicking.

With a campaign's approval set to "ask" (the default), nothing is generated on the agent's word
alone. The studio's Generate / Fix / Edit / Extend buttons first record a one-time approval for
exactly the run they send — the step, the model, the count, the length, the target — and the run
must carry it. A run the agent starts by itself (from a typed request) without one is not made:
it becomes a PROPOSAL on its step, shown pre-filled in the studio, where the user picks the model
and generates it, or dismisses it. With "auto", runs go ahead as asked.

Recording an approval is a WINDOW action (the tool that calls `approve` checks that), so a model
turn cannot approve its own run.
"""

from __future__ import annotations

from typing import Callable

from ad_generation.application.campaign_checklist_loader import CampaignChecklistLoader
from ad_generation.application.interfaces.campaign_store import CampaignStore

# What an approval pins: what the run costs and what it acts on. The prompt can be worded freely.
_PINNED = ("model", "count", "seconds", "resolution", "first_frame", "still", "clip", "mode")
_KEEP = 20  # approvals older than the last few were never used


class NeedsApproval(Exception):
    pass


class RunApprovals:
    def __init__(self, store: CampaignStore, loader: CampaignChecklistLoader, new_token: Callable[[], str]) -> None:
        self._store = store
        self._loader = loader
        self._new_token = new_token

    @staticmethod
    def pinned(args: dict) -> dict:
        return {k: str(args[k]) for k in _PINNED if args.get(k) not in (None, "", 0)}

    def approve(self, campaign_id: str, step_id: str, tool: str, args: dict) -> str:
        """The user clicked: this exact run may go ahead, once. -> its token."""
        checklist = self._loader.load(campaign_id)
        checklist.step(step_id)  # raises, naming the steps
        token = self._new_token()
        checklist.approvals = [
            *checklist.approvals[-(_KEEP - 1):],
            {"token": token, "step": step_id, "tool": tool, "args": self.pinned(args)},
        ]
        self._store.save_checklist(campaign_id, checklist)
        return token

    def admit(self, campaign_id: str, step_id: str, tool: str, args: dict, token: str) -> None:
        """Let the run go ahead, or raise NeedsApproval. An approval is used up by its run; a run
        clears its step's proposal."""
        checklist = self._loader.load(campaign_id)
        step = checklist.step(step_id)
        if checklist.approval != "auto":
            match = next(
                (a for a in checklist.approvals if token and a.get("token") == token and a.get("step") == step_id and a.get("tool") == tool),
                None,
            )
            if match is None or match.get("args") != self.pinned(args):
                raise NeedsApproval(f"step {step_id}: the user has not approved this run ({self.pinned(args)})")
            checklist.approvals = [a for a in checklist.approvals if a is not match]
        if step.proposal:
            checklist.put(step.with_(proposal={}))
        self._store.save_checklist(campaign_id, checklist)

    def propose(self, campaign_id: str, step_id: str, tool: str, args: dict) -> None:
        checklist = self._loader.load(campaign_id)
        checklist.put(checklist.step(step_id).with_(proposal={"tool": tool, "args": dict(args)}))
        self._store.save_checklist(campaign_id, checklist)

    def dismiss(self, campaign_id: str, step_id: str) -> None:
        checklist = self._loader.load(campaign_id)
        checklist.put(checklist.step(step_id).with_(proposal={}))
        self._store.save_checklist(campaign_id, checklist)

    def set_mode(self, campaign_id: str, mode: str) -> None:
        if mode not in ("ask", "auto"):
            raise ValueError(f"approval is 'ask' or 'auto', not '{mode}'")
        checklist = self._loader.load(campaign_id)
        checklist.approval = mode
        self._store.save_checklist(campaign_id, checklist)
