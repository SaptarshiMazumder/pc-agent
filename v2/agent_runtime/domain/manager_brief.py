"""ManagerBrief — everything the manager is shown at one checkpoint, and nothing else.

Lean by construction. The manager's value is an INDEPENDENT view: the stakeholder's own words, the
agreed contract, what the organisation can do, what actually ran, and what the developer claims.
Never the developer's transcript — the context that led it into the rabbit hole.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.domain.capability_sheet import CapabilitySheet
from agent_runtime.domain.deliverable_contract import DeliverableContract
from agent_runtime.domain.proof_result import ProofResult
from agent_runtime.domain.run_digest import RunDigest

ENGAGE = "engage"  # substantial work began: draft (or settle) the contract
MILESTONE = "milestone"  # the developer marked a plan step complete
DRIFT = "drift"  # activity without progress
FINISH = "finish"  # the developer is trying to end the turn
REPLY = "reply"  # the user answered while a contract awaited approval

CHECKPOINTS = (ENGAGE, MILESTONE, DRIFT, FINISH, REPLY)

_MAX_USER_CHARS = 2000


@dataclass(frozen=True)
class ManagerBrief:
    checkpoint: str
    user_messages: tuple[str, ...]  # the stakeholder, verbatim, oldest first
    contract: DeliverableContract | None
    sheet: CapabilitySheet
    digest: RunDigest
    developer_claim: str  # the developer's latest message to the user
    developer_plan: tuple[str, ...] = ()  # "status: step" lines from its latest update_plan
    proofs: tuple[ProofResult, ...] = ()
    drift: tuple[str, ...] = ()
    decisions: tuple[dict, ...] = ()
    app_context: tuple[str, ...] = ()  # notes the agent's app sent (app_context.py) — never requirements
    #: The developer's LAST tool calls and what they returned, whatever the checkpoint. The
    #: digest above is only what ran SINCE the last look — empty after a send-back the developer
    #: could not act on — so without these the manager judged a stop without its reason: a gate
    #: that refused, a service that was down, a hold the user placed.
    recent_results: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.checkpoint not in CHECKPOINTS:
            raise ValueError(f"unknown checkpoint {self.checkpoint!r}")

    def render(self) -> str:
        parts = [f"CHECKPOINT: {self.checkpoint}", "", "WHAT THE USER SAID (verbatim, oldest first):"]
        parts += [f"  > {m[:_MAX_USER_CHARS]}" for m in self.user_messages] or ["  (nothing)"]
        if self.app_context:
            parts += ["", "APP CONTEXT (from the app, NOT the user — where the work happens; never a requirement):"]
            parts += [f"  {m[:_MAX_USER_CHARS]}" for m in self.app_context]
        parts += ["", "CONTRACT:", self.contract.render() if self.contract else "  (none yet)"]
        if self.proofs:
            parts += ["", "PROOFS JUST CHECKED:"] + [f"  {p.render()}" for p in self.proofs]
        parts += ["", "WHAT ACTUALLY RAN SINCE THE LAST CHECKPOINT:", self.digest.render()]
        if self.checkpoint == FINISH and self.recent_results:
            parts += ["", "WHY THE DEVELOPER STOPPED — its last tool calls and what they returned:"]
            parts += list(self.recent_results)
        if self.drift:
            parts += ["", "DRIFT SIGNALS:"] + [f"  - {d}" for d in self.drift]
        parts += ["", "DEVELOPER'S PLAN:"] + ([f"  {p}" for p in self.developer_plan] or ["  (none)"])
        parts += ["", "DEVELOPER'S CLAIM (its latest words to the user):", self.developer_claim or "  (none)"]
        if self.decisions:
            parts += ["", "YOUR RECENT DECISIONS:"]
            parts += [
                f"  - {d.get('checkpoint')}: {d.get('kind')} {d.get('criterion_id') or ''} — {d.get('reason') or ''}"
                for d in self.decisions
            ]
        parts += ["", self.sheet.render()]
        return "\n".join(parts)


__all__ = ["CHECKPOINTS", "DRIFT", "ENGAGE", "FINISH", "MILESTONE", "REPLY", "ManagerBrief"]
