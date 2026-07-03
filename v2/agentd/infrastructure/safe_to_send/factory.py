"""build_safe_to_send_gate — compose the egress privacy gate (judge LLM + gate impl).

The judge LLM call is built here (the one place that knows LiteLLM) and injected into
LlmSafeToSendGate, so the gate impl stays decoupled from it. Returns None when the gate is
disabled OR no model is available — in which case the gateway sends replies unchanged
(fail-SAFE for the not-configured case; the gate only FAILS-CLOSED at runtime once active).
"""

from __future__ import annotations

import logging

from agentd.infrastructure.safe_to_send.gate import JudgeFn, LlmSafeToSendGate

log = logging.getLogger("agentd")


def _build_judge_fn(config) -> JudgeFn | None:
    """A cheap async judge(prompt)->text, resolved from the unified plugins map
    (plugins.safe_to_send.safe_to_send -> verify -> search -> brain)."""
    from agentd.application.tool_models import safe_to_send_model
    model = safe_to_send_model(config)
    if not model:
        return None

    async def judge_fn(prompt: str) -> str:
        import litellm

        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return resp.choices[0].message.content or ""

    return judge_fn


def build_safe_to_send_gate(config):
    """Build the gate, or None if disabled / no model. None => replies are not gated."""
    if not getattr(config, "safe_to_send_check", False):
        return None
    judge = _build_judge_fn(config)
    if judge is None:
        log.warning("safe-to-send gate enabled but no judge model available — gate is OFF")
        return None
    return LlmSafeToSendGate(judge)
