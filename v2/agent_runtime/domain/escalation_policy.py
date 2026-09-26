"""EscalationPolicy — when repeated failure stops being "try again" and becomes a different move.

Pure rules over the ledger, deliberately NOT left to the manager model: an LLM asked "should we
escalate?" will escalate on the first hard problem, which is the giving-up this exists to prevent.

    failures on a criterion < FAILURES_BEFORE_RETHINK   -> the manager's REDIRECT stands
    reaches it                                          -> RETHINK: list assumptions, research,
                                                           take a genuinely different approach
    rethinks on it reach RETHINKS_BEFORE_ESCALATION     -> stop steering; the work ends as it stands
"""

from __future__ import annotations

from dataclasses import replace

from agent_runtime.domain.manager_ledger import ManagerLedger
from agent_runtime.domain.manager_verdict import ESCALATE, REDIRECT, RETHINK, ManagerVerdict

RETHINK_DIRECTIVE = (
    "This criterion has failed repeatedly, so the approach is wrong, not the effort. Think it "
    "through privately before your next action: which assumption do the failures contradict, and "
    "what DIFFERENT approach avoids it? Then take that approach. Do not write this reasoning to "
    "the user, and do not repeat the approach that failed."
)

#: How a question for the person is put, when only they can clear the way: in the agent's own
#: voice, one short ask, nothing about blockers, attempts or any manager.
ASK_USER_DIRECTIVE = (
    "Ask the user, briefly and in your own normal voice, for exactly this and nothing else: {ask} "
    "Do not describe what you tried, list blockers, or mention any review. Then end your turn."
)


class EscalationPolicy:
    def __init__(self, failures_before_rethink: int = 3, rethinks_before_escalation: int = 2):
        self._failures_before_rethink = failures_before_rethink
        self._rethinks_before_escalation = rethinks_before_escalation

    def adjust(self, verdict: ManagerVerdict, ledger: ManagerLedger) -> ManagerVerdict:
        """The verdict to act on, given the history. Only a REDIRECT about a criterion changes."""
        cid = verdict.criterion_id
        if verdict.kind != REDIRECT or not cid:
            return verdict
        if ledger.failures.get(cid, 0) < self._failures_before_rethink:
            return verdict
        if ledger.rethinks.get(cid, 0) < self._rethinks_before_escalation:
            return replace(verdict, kind=RETHINK, directive=f"{RETHINK_DIRECTIVE}\n{verdict.directive}")
        # Rethinks exhausted and nothing the person could do was identified: stop steering and
        # let the work end as it stands. A generated question ("enable command execution") is
        # noise to a person; only a concrete ask of theirs is worth their attention.
        return replace(verdict, kind=ESCALATE, directive="")


__all__ = ["ASK_USER_DIRECTIVE", "RETHINK_DIRECTIVE", "EscalationPolicy"]
