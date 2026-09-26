"""EscalationPolicy — when repeated failure stops being "try again" and becomes a different move.

Pure rules over the ledger, deliberately NOT left to the manager model: an LLM asked "should we
escalate?" will escalate on the first hard problem, which is the giving-up this exists to prevent.

    failures on a criterion < FAILURES_BEFORE_RETHINK   -> the manager's REDIRECT stands
    reaches it                                          -> RETHINK: list assumptions, research,
                                                           take a genuinely different approach
    rethinks on it reach RETHINKS_BEFORE_ESCALATION     -> ESCALATE to the user, with what was tried
"""

from __future__ import annotations

from dataclasses import replace

from agent_runtime.domain.manager_ledger import ManagerLedger
from agent_runtime.domain.manager_verdict import ESCALATE, REDIRECT, RETHINK, ManagerVerdict

RETHINK_DIRECTIVE = (
    "RETHINK. This criterion has failed repeatedly with the current approach, so the approach is "
    "wrong, not the effort. Before your next action: (1) list the assumptions you have been "
    "working under, (2) name which one the failures contradict, (3) research alternatives if you "
    "need to, (4) choose a DIFFERENT approach and say which. Do not repeat the one that failed."
)

ESCALATE_DIRECTIVE = (
    "ESCALATE. Send the user a message in EXACTLY this shape — short bullets, no essay, no file "
    "names or internals — then END YOUR TURN. Do not claim it is done.\n"
    "**Blocked on**\n- <one line: what cannot be done yet>\n"
    "**Tried**\n- <up to 3 bullets, one line each: approach and what it returned>\n"
    "**Need from you**\n- <the one thing only they can provide>\n"
    "Reply with <exactly what they should reply or do>."
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
        return replace(verdict, kind=ESCALATE, directive=ESCALATE_DIRECTIVE)


__all__ = ["ESCALATE_DIRECTIVE", "RETHINK_DIRECTIVE", "EscalationPolicy"]
