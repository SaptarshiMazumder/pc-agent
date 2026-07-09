"""verify_answer tool — agent-invoked, in-loop answer review (the recommended
verifier path; replaces the post-stream loop-hook that caused double answers).

The agent calls this on its DRAFT before presenting a final answer, reads the
verdict as a normal tool result, and (if needed) revises SILENTLY — then sends one
clean answer. No second message, no apology to feedback the user never gave.

Decoupled: the tool wraps a Verifier (the LLM-judge), nothing else.
"""

from __future__ import annotations

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.interfaces.verifier import Verifier, VerifyContext


class VerifyTool(Tool):
    name = "verify_answer"
    default_timeout_sec = 60.0
    default_retryable = True
    default_max_retries = 1
    description = (
        "Independently review YOUR OWN draft answer BEFORE you send it, to catch a missing "
        "part, an unsupported/fabricated claim, or an answer that only promises instead of "
        "delivering. Use it as a plan step for any substantial answer (lists, research, "
        "multi-step results) — pass the full draft you intend to send.\n"
        "Returns 'PASS' or 'NEEDS WORK: <issues>'. If it needs work, FIX the issues silently "
        "and then present ONE clean final answer — do NOT apologize, and do NOT mention this "
        "review or address feedback the user did not actually give."
    )
    label = "Verify Answer"
    parameters = {
        "type": "object",
        "required": ["answer"],
        "properties": {
            "answer": {
                "type": "string",
                "description": "The full draft final answer you intend to send, to be reviewed.",
            },
            "task": {
                "type": "string",
                "description": "What the user asked for (the requirement to check against).",
            },
            "evidence": {
                "type": "string",
                "description": "Key facts / tool output that back the answer (helps catch fabrication).",
            },
        },
    }

    def __init__(self, config, verifier: Verifier):
        self.config = config
        self._verifier = verifier

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ctx = VerifyContext(
            task=params.get("task") or "(infer the requirement from the answer)",
            answer=params.get("answer", ""),
            evidence=[params["evidence"]] if params.get("evidence") else [],
        )
        verdict = await self._verifier.verify(ctx)
        if verdict.ok:
            return ToolResult.text(
                "PASS — the draft looks complete and supported. Send it as your final answer."
            )
        return ToolResult.text(
            f"NEEDS WORK: {verdict.reasons or 'the draft is incomplete or unsupported.'}\n\n"
            "Fix these issues, then present ONE clean final answer. Do not apologize or mention "
            "this review to the user."
        )
