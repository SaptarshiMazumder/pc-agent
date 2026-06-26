"""LlmSafeToSendGate — an independent second opinion on whether a draft reply is SAFE TO SEND.

DECOUPLED: depends ONLY on an injected async `judge_fn(prompt) -> text` and a rubric string.
It knows nothing about tools, sessions, channels, or how the LLM is called. It reads the
agent's OWN operating rules + the reply + who it's going to, and returns a Verdict.

FAIL-CLOSED (the opposite of the quality verifier): any error — judge call fails, output
unparseable — returns safe=False (BLOCK), because a privacy gate that can't decide must not
let an unchecked reply through. A safe replacement is always provided so the recipient still
gets a sensible message. (Note: this only triggers when the gate is ACTIVE; if no judge model
is configured the gate isn't built at all and replies pass through unchanged — see factory.)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Awaitable, Callable

from agentd.application.interfaces.safe_to_send import SafeToSendContext, SafeToSendVerdict

log = logging.getLogger("agentd")

JudgeFn = Callable[[str], Awaitable[str]]

# Sent in place of a blocked reply when the judge didn't supply its own safe_reply.
DEFAULT_SAFE_REPLY = (
    "Sorry, I'm not able to share that here. Could you give me a few more details about "
    "your own request so I can help you directly?")

DEFAULT_RUBRIC = (
    "You are a disclosure gate for an AI assistant. The assistant just wrote a REPLY to send to "
    "a user on a PUBLIC messaging channel. Your ONLY standard is the agent's OPERATING "
    "RULES, given below — you do NOT add, assume, or invent any rules of your own. If the rules "
    "are silent on something, that is NOT a violation.\n"
    "Decide one thing: would SENDING this reply to THIS recipient VIOLATE those rules? Use the "
    "RECENT CONVERSATION to judge it in context — who you are talking to and what has been said "
    "earlier, including whether any condition the rules require has already been met.\n"
    "Block (safe=false) ONLY if sending the reply would clearly break one of those rules; "
    "otherwise allow (safe=true). Judge strictly by the rules, not by your own instincts — do "
    "not block on a hunch, and a wrongly-blocked, helpful answer is itself a real failure.\n"
    "If you BLOCK, write a brief safe_reply that itself obeys the rules; do NOT invent any "
    "process, contact method, or detail not present in the conversation. If safe=true, leave "
    "safe_reply empty [VERY IMPORTANT, DO NOT INVENT ANSWERS].\n"
    'Reply with ONLY compact JSON: {"safe": true|false, "reason": "<short>", '
    '"safe_reply": "<text or empty>"}')


class LlmSafeToSendGate:
    def __init__(self, judge_fn: JudgeFn, rubric: str = DEFAULT_RUBRIC,
                 max_policy_chars: int = 12_000, max_convo_chars: int = 6_000,
                 safe_reply: str = DEFAULT_SAFE_REPLY):
        self._judge = judge_fn
        self._rubric = rubric
        self._max_policy = max_policy_chars
        self._max_convo = max_convo_chars
        self._safe = safe_reply

    async def check(self, ctx: SafeToSendContext) -> SafeToSendVerdict:
        try:
            prompt = self._build_prompt(ctx)
            raw = await self._judge(prompt)
            data = _extract_json(raw)
            if data is None:
                return self._blocked("gate returned unparseable output")   # FAIL-CLOSED
            if bool(data.get("safe", False)):
                return SafeToSendVerdict(safe=True)
            return SafeToSendVerdict(
                safe=False,
                reason=str(data.get("reason", "")).strip(),
                safe_reply=(str(data.get("safe_reply", "")).strip() or self._safe))
        except Exception as e:  # noqa: BLE001 — a broken gate must FAIL CLOSED (block), not leak
            log.warning("safe-to-send gate failed (fail-closed -> block): %s", e)
            return self._blocked(f"gate error: {type(e).__name__}")

    def _blocked(self, reason: str) -> SafeToSendVerdict:
        return SafeToSendVerdict(safe=False, reason=reason, safe_reply=self._safe)

    def _build_prompt(self, ctx: SafeToSendContext) -> str:
        policy = (ctx.policy or "(no explicit rules)")[: self._max_policy]
        convo = (ctx.conversation or "").strip()
        convo = convo[-self._max_convo:] if convo else "(none provided)"
        return (
            f"{self._rubric}\n\n"
            f"## RECIPIENT\n"
            f"audience: {ctx.audience} — a member of the public on a messaging channel, NOT "
            f"the owner/operator/admin.\n\n"
            f"## THE ASSISTANT'S OWN OPERATING RULES (authoritative)\n{policy}\n\n"
            f"## RECENT CONVERSATION (oldest first — context for judging the reply against the rules)\n{convo}\n\n"
            f"## WHAT THE RECIPIENT ASKED (latest)\n{ctx.question}\n\n"
            f"## THE REPLY ABOUT TO BE SENT\n{ctx.answer}\n\n"
            f"## YOUR VERDICT (JSON only)")


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
