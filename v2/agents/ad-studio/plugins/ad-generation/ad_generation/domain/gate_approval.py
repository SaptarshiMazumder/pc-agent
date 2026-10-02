"""The conversation's latest ask and its answer — what decides whether a gate may be passed.

A gate's results are made at `gate_reached_at`. The user approves by answering an ask that was
shown AFTER those results existed: an ask from before them asked about something else, and an
ask with no answer yet has not been approved by anyone.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateApproval:
    presented_at: float  # when the latest campaign_ask returned in this conversation; 0 = never
    answered_at: float  # when the user's next message arrived; 0 = not yet

    def refusal(self, gate: str, gate_reached_at: float) -> str:
        """Why the campaign may not move past `gate`, or "" when the user has answered for it."""
        if self.presented_at < gate_reached_at:
            return (
                f"the {gate} results have not been put to the user. Show them, call campaign_ask, "
                "then end the turn."
            )
        if self.answered_at < self.presented_at:
            return (
                f"the user has not answered the {gate} ask yet. Do nothing more this turn — their "
                "answer arrives as their next message."
            )
        return ""
