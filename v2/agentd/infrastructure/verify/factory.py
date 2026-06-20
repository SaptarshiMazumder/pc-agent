"""build_judge_fn — the cheap LLM-judge call used by the `verify_answer` tool.

The judge LLM call is built here (the one place that knows LiteLLM) and injected
into LlmJudgeVerifier, so the verifier impl + the tool stay decoupled from it."""

from __future__ import annotations

import logging

from agentd.infrastructure.verify.llm_judge import JudgeFn

log = logging.getLogger("agentd")


def build_judge_fn(config) -> JudgeFn | None:
    """An async judge(prompt)->text backed by a cheap model (verify_model >
    search_model > model). Returns None if no model is available."""
    model = (
        getattr(config, "verify_model", None)
        or getattr(config, "search_model", None)
        or getattr(config, "model", None)
    )
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
