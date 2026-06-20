"""Verifier — the QUALITY contract ("is the answer good / complete / honest?").

A SECOND opinion on a draft answer. It is consumed by the agent-invoked
`verify_answer` TOOL (not the loop): the agent passes its draft, the verifier
grades it, and the agent fixes issues before replying. A model grading its own
single response in-band is weak, so the real check is out-of-band (a separate
judge call) — which is exactly what the tool's verifier does.

DECOUPLED: an implementation depends only on what it needs (the LLM-judge needs an
injected judge function + a rubric — not the tools, session, loop, or gateway).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class VerifyContext:
    task: str                       # what the user asked for (latest user message)
    answer: str                     # the agent's proposed final answer
    evidence: list = field(default_factory=list)  # recent tool-result summaries (proof)


@dataclass(frozen=True)
class Verdict:
    ok: bool                        # True => accept the answer
    reasons: str = ""               # if not ok: what's wrong (fed back to the agent)


@runtime_checkable
class Verifier(Protocol):
    async def verify(self, ctx: VerifyContext) -> Verdict: ...
