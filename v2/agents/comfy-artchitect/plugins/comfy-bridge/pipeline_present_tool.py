"""pipeline_present — the approval card, built from the design that was CHECKED, not from memory.

The end of Phase 1. Refuses unless the pipeline holds (pipeline_validate). Then it builds the card
the window renders and the run gate waits on — from the stages themselves (ApprovalCardBuilder) —
and SHOWS it: this tool is the checkpoint (`checkpoint = True`; its `details["ask"]` is the card, in
ask_user's shape, each step with its facts: model, inputs, size, length, prompt).

The turn ends when it returns (the engine stops after a shown checkpoint). Nothing is downloaded or
rented before the answer: setup and runs refuse while the card is unanswered, when the answer asks
for a change ("Instead: …"), and for a card that is not this one (studio_state.checkpoint_answered).
After it, each stage still runs only when the person presses Run on it (StageRunApprovals).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import studio_state
from approval_card_builder import ApprovalCardBuilder
from pipeline_tool_context import PipelineToolContext
from pipeline_validator import render_report


class PipelinePresentTool(Tool):
    name = "pipeline_present"
    label = "Show the approval card"
    default_retryable = True
    #: THE CARD IS SHOWN BY THIS TOOL — it is the checkpoint (the daemon stamps it and ends the
    #: turn, as for ask_user). The model used to copy the card into ask_user by hand: it forgot,
    #: curled a quote in a 3,000-character prompt (refused as "a different card"), or wrote "the
    #: card is open" with nothing shown. Nothing to copy, nothing to get wrong.
    checkpoint = True
    description = (
        "When the design holds and every ? is answered: shows the person the approval card — every "
        "stage with its model, inputs, size, length and full prompt, and what Comfy Cloud imports. "
        "Give `answers`: for each ? in the report (q1, q2, …) one line on why the design is right as it "
        "is — or fix the design instead and validate again. The card is on screen when this returns; "
        "the person's answer is their next message."
    )
    parameters = {
        "type": "object",
        "properties": {
            "answers": {
                "type": "object",
                "description": '{"q1": "one line on why this is right for the job", …} — one per ? in the '
                               "current report. A ? you agree with is fixed in the design, not answered.",
            },
        },
    }

    def __init__(self, context: Callable[[], PipelineToolContext] | None = None,
                 card: Callable[[PipelineToolContext], ApprovalCardBuilder] | None = None) -> None:
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))
        self._card = card or (lambda ctx: ApprovalCardBuilder(ctx.builder, ctx.facts))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            ctx = self._context()
            pipeline = ctx.store.load()
            if pipeline is None:
                return ToolResult.text("no pipeline in this chat yet — pipeline_plan first", is_error=True)
            report = ctx.validate(pipeline)
            if not report.holds:
                return ToolResult.text("the design does not hold yet — fix it before asking:\n"
                                       + render_report(report), is_error=True)
            # EVERY ? IS LOOKED AT. A finding the agent read past is how a video made straight from a
            # raw selfie reached the card with its warning still open.
            raw = params.get("answers") or {}
            if not isinstance(raw, dict):
                return ToolResult.text('`answers` is an object keyed by number: {"q1": "one line", "q2": "…"}',
                                       is_error=True)
            given = {str(k).strip().lower(): str(v).strip() for k, v in raw.items()}
            open_q = [f"q{i}: {q}" for i, q in enumerate(report.questions, 1) if not given.get(f"q{i}")]
            if open_q:
                return ToolResult.text(
                    "every ? needs an answer before the card — fix it in the design (then validate again), "
                    "or pass `answers` with one line per number on why it is right for this job. Unanswered:\n  "
                    + "\n  ".join(open_q), is_error=True)
            card = self._card(ctx).build(pipeline, {s.name: ctx.store.graph(s) for s in pipeline.stages},
                                         report, ctx.cloud_files())
            studio_state.record_card(card, [s.name for s in pipeline.stages])  # the gate checks the person saw THIS
            answered = "".join(f"\n  q{i}: {given[f'q{i}']}" for i in range(1, len(report.questions) + 1))
            steps = "".join(f"\n  {w['name']} — {w['does']}" for w in card["workflows"])
            return ToolResult.text(
                f"The approval card is on screen: {card['title']}{steps}\n"
                "The person's answer is their next message — a yes (with any prompt they edited), or "
                "'Instead: …', which is a change to the design. After a yes and setup, each stage runs "
                "when they press Run on it in the Stages panel."
                + (f"\n\nyour answers to the design's ? findings:{answered}" if answered else ""),
                details={"ask": card},
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"pipeline_present failed: {type(e).__name__}: {e}", is_error=True)


__all__ = ["PipelinePresentTool"]
