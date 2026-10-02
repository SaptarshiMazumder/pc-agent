"""Where the conversation's latest ask and its answer are recorded."""

from __future__ import annotations

from typing import Protocol

from ad_generation.domain.gate_approval import GateApproval


class ApprovalLedger(Protocol):
    def latest(self) -> GateApproval:
        """This conversation's latest ask and answer. Raises when the conversation cannot be
        identified — a gate is never passed on a guess."""
        ...
