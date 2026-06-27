"""Built-in 'verify' bundle — the agent-invoked answer-review tool (verify_answer).

Gating ported from build_tools: registers only when ``verify_tool`` is enabled AND a judge model
builds. OFF => not registered at all, exactly as if the feature never existed. The judge factory
lives in agentd.infrastructure.verify (an inner layer this plugin reads — allowed direction).
"""

from __future__ import annotations


# The verify-before-send DIRECTIVE — system-prompt guidance, shown only when verify_answer is in
# the toolset (the tool's `description` covers the tool itself; this is the behavioral rule).
_VERIFY_GUIDANCE = (
    "## Verify Before You Send (required)\n"
    "For any substantial answer (lists, research, multi-step results, factual claims, or anything "
    "you deliver/email), you MUST call `verify_answer` with your full draft BEFORE replying. If it "
    "returns NEEDS WORK, you MUST fix the issues and re-verify — do NOT send an answer that failed "
    "verification. Loop until it passes, or stop and report the blocker honestly. The review is "
    "internal; the user sees only the finished answer (never apologize for feedback they didn't give)."
)


def _verify_section(tools, agent, config) -> str:
    return _VERIFY_GUIDANCE if any(getattr(t, "name", "") == "verify_answer" for t in tools) else ""


def register(api, ctx):
    if not getattr(ctx.config, "verify_tool", False):
        return
    from agentd.infrastructure.verify import LlmJudgeVerifier, build_judge_fn

    judge = build_judge_fn(ctx.config)
    if judge is None:
        return
    from verify_tool import VerifyTool

    api.register_tool(VerifyTool(ctx.config, LlmJudgeVerifier(judge)))
    api.register_prompt_section(_verify_section)
