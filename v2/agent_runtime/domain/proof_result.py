"""ProofResult — what checking one contract criterion found.

`passed` is None for a `judged` criterion: nothing machine-checkable was run, and the manager
decides from the evidence it is shown.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProofResult:
    criterion_id: str
    passed: bool | None
    evidence: str  # short: what was checked and what came back

    def render(self) -> str:
        mark = {True: "PROVEN", False: "NOT PROVEN", None: "TO JUDGE"}[self.passed]
        return f"[{self.criterion_id}] {mark}: {self.evidence}"


__all__ = ["ProofResult"]
