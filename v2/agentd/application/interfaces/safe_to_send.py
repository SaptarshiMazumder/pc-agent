"""SafeToSendGate — the PRIVACY contract ("is this reply SAFE TO SEND to THIS recipient?").

An out-of-band check on a draft reply BEFORE it leaves on an external surface (a public
messaging channel). It is the enforcement half of the agent's own operating rules: the rules
live in the agent's AGENTS.md (authored by the operator); this gate is a SEPARATE judgment
that reads those rules and can VETO an outbound reply that breaks them.

Why a second actor: an agent enforcing its own rules in-band is not a boundary — it is the
same model that just wrote the reply, biased toward being helpful and reachable by the
conversation. The gate is independent: it only sees (rules, recipient, reply) and answers one
question — is this safe to send? It cannot be talked out of it and runs every time.

DECOUPLED: an implementation depends only on what it needs (the LLM gate needs an injected
judge function + a rubric — not the tools, session, loop, or gateway).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SafeToSendContext:
    audience: str  # who the reply is going to (e.g. "external" — a public-channel recipient)
    policy: str  # the agent's OWN operating rules (its AGENTS.md / bootstrap)
    question: str  # what the recipient asked
    answer: str  # the reply about to be sent
    conversation: str = ""  # recent dialog (so the judge can tell the recipient's OWN info,
    #                                 and whether they've identified themselves, from an actual leak)


@dataclass(frozen=True)
class SafeToSendVerdict:
    safe: bool  # True => deliver the reply as-is
    reason: str = ""  # if blocked: why (for the audit log)
    safe_reply: str = ""  # if blocked: a safe replacement to send instead


@runtime_checkable
class SafeToSendGate(Protocol):
    async def check(self, ctx: SafeToSendContext) -> SafeToSendVerdict: ...
