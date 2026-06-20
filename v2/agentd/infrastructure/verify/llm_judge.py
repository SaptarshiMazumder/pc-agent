"""LlmJudgeVerifier — an out-of-band second opinion on the final answer.

DECOUPLED: it depends ONLY on an injected async `judge_fn(prompt) -> text` and a
rubric string. It knows nothing about tools, sessions, the loop, or how the LLM is
called. It grades the answer against the task on three axes — completeness,
evidence-backing, and no-fabrication — and returns a Verdict.

FAIL-OPEN: any error (judge call fails, unparseable output) returns ok=True, so a
broken verifier can never block a run.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Awaitable, Callable

from agentd.application.interfaces.verifier import Verdict, VerifyContext

log = logging.getLogger("agentd")

JudgeFn = Callable[[str], Awaitable[str]]

DEFAULT_RUBRIC = (
    "You are a strict reviewer grading an AI assistant's FINAL ANSWER to a user TASK.\n"
    "Fail the answer (ok=false) if ANY of these is true:\n"
    "  - INCOMPLETE: it does not address every part of the task (e.g. asked for 5 items, "
    "gave 3; asked to do X and Y, only did X).\n"
    "  - UNSUPPORTED/FABRICATED: it states facts, URLs, names, or numbers that are not "
    "backed by the evidence (tool output) — e.g. invented links or guessed profiles.\n"
    "  - EVADES: it promises to do the task or describes a plan instead of actually "
    "delivering the result.\n"
    "Otherwise pass (ok=true). A partial answer that HONESTLY states what it couldn't get "
    "is acceptable (ok=true) — only fail it if it could have done more or made things up.\n"
    'Reply with ONLY compact JSON: {"ok": true|false, "reasons": "<one or two sentences; '
    'concrete and actionable if false>"}'
)


class LlmJudgeVerifier:
    def __init__(self, judge_fn: JudgeFn, rubric: str = DEFAULT_RUBRIC, max_evidence: int = 6):
        self._judge = judge_fn
        self._rubric = rubric
        self._max_evidence = max_evidence

    async def verify(self, ctx: VerifyContext) -> Verdict:
        try:
            prompt = self._build_prompt(ctx)
            raw = await self._judge(prompt)
            data = _extract_json(raw)
            if data is None:
                return Verdict(ok=True)  # fail-open on unparseable output
            ok = bool(data.get("ok", True))
            return Verdict(ok=ok, reasons="" if ok else str(data.get("reasons", "")).strip())
        except Exception as e:  # noqa: BLE001 — a broken verifier must never block a run
            log.warning("verifier failed (fail-open): %s", e)
            return Verdict(ok=True)

    def _build_prompt(self, ctx: VerifyContext) -> str:
        evidence = "\n".join(f"- {e}" for e in (ctx.evidence or [])[: self._max_evidence]) or "(none)"
        return (
            f"{self._rubric}\n\n"
            f"## TASK\n{ctx.task}\n\n"
            f"## EVIDENCE (tool output the assistant actually obtained)\n{evidence}\n\n"
            f"## FINAL ANSWER\n{ctx.answer}\n\n"
            f"## YOUR VERDICT (JSON only)"
        )


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", text, re.DOTALL)  # first {...} block
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return None
