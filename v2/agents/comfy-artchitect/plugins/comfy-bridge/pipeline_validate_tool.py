"""pipeline_validate — the whole design, checked: does it hold, and what must be answered.

Every stage against the node list of the box's ComfyUI (no GPU) and the knowledge base's rules for
its models; then the pipeline — bindings, order, types, disk, versions, packs. Errors (✗) must be
fixed; questions (?) answered — fixed, or said in one line why they are right for this job. The
design is done when this holds; only then is it shown to the user.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from pipeline_tool_context import PipelineToolContext
from pipeline_validator import render_report


class PipelineValidateTool(Tool):
    name = "pipeline_validate"
    label = "Check the whole design"
    default_retryable = True
    description = (
        "Check this chat's pipeline end to end without a GPU: every stage against the node list and "
        "the models' rules, then bindings, order, media types, disk and versions. Fix every ✗; answer "
        "every ? (fix it, or one line on why it is right). The design is done only when it holds."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            ctx = self._context()
            pipeline = ctx.store.load()
            if pipeline is None:
                return ToolResult.text("no pipeline in this chat yet — pipeline_plan first", is_error=True)
            report = ctx.validate(pipeline)
            return ToolResult.text(render_report(report), details={"holds": report.holds},
                                   is_error=not report.holds)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"pipeline_validate failed: {type(e).__name__}: {e}", is_error=True)


__all__ = ["PipelineValidateTool"]
