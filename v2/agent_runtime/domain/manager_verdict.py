"""ManagerVerdict — what the project manager decides at a checkpoint.

The manager is a separate model call with a lean, independent view of the work (see
application/services/manager_checkpoint_service.py). At each checkpoint it returns ONE of these:

    CONTINUE        on track — say nothing; the small calls are the developer's
    REDIRECT        wrong rabbit hole or a false blocker — here is the concrete next move
    RETHINK         the same criterion keeps failing — stop, list assumptions, take a different
                    approach (research allowed)
    AWAIT_APPROVAL  the contract needs the user's yes before the work starts
    DONE            every criterion of the contract is proven
    ESCALATE        rethinks are exhausted — put the question to the user, with what was tried

Everything the manager says to the developer is injected into the run behind MANAGER_PREFIX, so
the runtime can always tell it from the person (a manager note must never read as the user).
"""

from __future__ import annotations

from dataclasses import dataclass

MANAGER_PREFIX = "[manager]"

CONTINUE = "continue"
REDIRECT = "redirect"
RETHINK = "rethink"
AWAIT_APPROVAL = "await_approval"
DONE = "done"
ESCALATE = "escalate"

VERDICT_KINDS = (CONTINUE, REDIRECT, RETHINK, AWAIT_APPROVAL, DONE, ESCALATE)


@dataclass(frozen=True)
class ManagerVerdict:
    kind: str
    directive: str = ""  # what the developer is told to do next ("" for CONTINUE / DONE)
    criterion_id: str = ""  # the contract criterion this is about, when there is one
    reason: str = ""  # one line on WHY — kept in the ledger, never shown to the developer

    def __post_init__(self) -> None:
        if self.kind not in VERDICT_KINDS:
            raise ValueError(f"unknown verdict kind {self.kind!r} (expected one of {VERDICT_KINDS})")

    @classmethod
    def from_dict(cls, d: dict) -> "ManagerVerdict":
        return cls(
            kind=str(d.get("kind") or "").strip().lower(),
            directive=str(d.get("directive") or "").strip(),
            criterion_id=str(d.get("criterion_id") or "").strip(),
            reason=str(d.get("reason") or "").strip(),
        )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "directive": self.directive,
            "criterion_id": self.criterion_id,
            "reason": self.reason,
        }


__all__ = [
    "AWAIT_APPROVAL",
    "CONTINUE",
    "DONE",
    "ESCALATE",
    "MANAGER_PREFIX",
    "REDIRECT",
    "RETHINK",
    "VERDICT_KINDS",
    "ManagerVerdict",
]
