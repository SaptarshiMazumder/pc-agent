"""ManagerLedger — the manager's own memory of one piece of work.

Not a second transcript: the contract, the decisions taken, and the counts the escalation policy
reads. It lives beside the session (a few KB) and survives across runs, so "continue" tomorrow
resumes against the same agreed contract rather than starting over.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.domain.deliverable_contract import DeliverableContract
from agent_runtime.domain.manager_verdict import ManagerVerdict

MAX_DECISIONS_KEPT = 50


@dataclass
class ManagerLedger:
    contract: DeliverableContract | None = None
    decisions: list[dict] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)  # criterion -> failed finish attempts
    rethinks: dict[str, int] = field(default_factory=dict)  # criterion -> rethinks ordered
    awaiting_user: bool = False  # the developer was told to put something to the user

    def record(self, checkpoint: str, verdict: ManagerVerdict, at: float) -> None:
        self.decisions.append({"at": at, "checkpoint": checkpoint, **verdict.to_dict()})
        del self.decisions[:-MAX_DECISIONS_KEPT]

    def note_failure(self, criterion_id: str) -> None:
        self.failures[criterion_id] = self.failures.get(criterion_id, 0) + 1

    def note_rethink(self, criterion_id: str) -> None:
        self.rethinks[criterion_id] = self.rethinks.get(criterion_id, 0) + 1
        self.failures[criterion_id] = 0

    def recent_decisions(self, n: int = 8) -> list[dict]:
        return self.decisions[-n:]

    def to_dict(self) -> dict:
        return {
            "contract": self.contract.to_dict() if self.contract else None,
            "decisions": self.decisions,
            "failures": self.failures,
            "rethinks": self.rethinks,
            "awaiting_user": self.awaiting_user,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ManagerLedger":
        raw = d.get("contract")
        return cls(
            contract=DeliverableContract.from_dict(raw) if raw else None,
            decisions=list(d.get("decisions") or []),
            failures={str(k): int(v) for k, v in (d.get("failures") or {}).items()},
            rethinks={str(k): int(v) for k, v in (d.get("rethinks") or {}).items()},
            awaiting_user=bool(d.get("awaiting_user")),
        )


__all__ = ["ManagerLedger"]
