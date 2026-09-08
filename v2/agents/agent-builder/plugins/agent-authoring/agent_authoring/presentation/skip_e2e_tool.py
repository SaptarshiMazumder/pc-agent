"""skip_e2e — record that the USER waived testing for this run, and why.

THE ONLY DOOR OUT of the e2e gate, and it is deliberately narrow. The gate stops a batch of
building from being called finished until a scenario has proved it; that is the point, and an
escape the model can reach for on its own would put the point back where it started.

So this tool is not a way to decide testing is unnecessary. It records a decision the USER
already made, in their words, into the transcript where they can see it. The reason is required
for exactly that reason: "skipped" with no sentence beside it is indistinguishable from an agent
that could not be bothered.

IT LASTS FOR THIS RUN. The next batch asks again -- somebody who skipped one test has not signed
a blanket exemption, and the run-scoped lifetime is not enforced here but by construction: the
gate is a RunObserver and the loop resets it when the next run starts.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class SkipE2eTool(Tool):
    name = "skip_e2e"
    label = "Skip Tests (user waived)"
    default_retryable = False
    description = (
        "RECORD THAT THE USER WAIVED TESTING for this run. Call it ONLY after the person you "
        "are talking to has said, in their own words, to skip the tests -- never on your own "
        "judgement, and never because a test is inconvenient, slow, or failing.\n"
        "If a test needs something only they have (a live URL, an API key, an account), the "
        "answer is to ASK THEM FOR IT, not to call this. If a change genuinely cannot be tested "
        "by a scenario because it is only window source, `verify_app` is the proof for that -- "
        "also not this.\n"
        "The waiver covers THIS RUN only: the next batch of work asks again."
    )
    parameters = {
        "type": "object",
        "required": ["reason"],
        "properties": {
            "reason": {
                "type": "string",
                "description": "What the user actually said, in their terms -- 'user said skip "
                "tests, no ComfyUI instance running right now'. It goes in the transcript.",
            },
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        reason = str(params.get("reason") or "").strip()
        if not reason:
            return ToolResult.text(
                "skip_e2e needs the user's `reason` -- their own words. A waiver with nothing "
                "beside it is indistinguishable from skipping the tests quietly.",
                is_error=True,
            )
        return ToolResult.text(
            "Testing waived for this run: " + reason + "\n"
            "The next batch of work will ask again."
        )
