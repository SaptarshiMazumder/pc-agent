"""stage_bind — say where a stage's media input comes from: the person's file, or an earlier stage.

`user:<role>` is a reference slot the person fills in the References panel (several stages may read
the same role). `stage:<name>.<output>` is what an earlier stage produced; the runtime hands it over
when that stage has run, so no filename is ever carried by hand. Checked at once: the input exists,
the producer is earlier, it makes that output, and the media types match.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from pipeline import StageInput
from pipeline_run_record import PipelineRunRecord
from pipeline_tool_context import PipelineToolContext
from pipeline_validator import render_report
from stage_builder import StageBuildError


class StageBindTool(Tool):
    name = "stage_bind"
    label = "Wire a stage's input"
    default_retryable = False
    description = (
        "Bind a stage's media input to `user:<role>` (a file the person adds, e.g. user:photo) or "
        "`stage:<earlier stage>.<output>` (e.g. stage:keyframe.image), then re-check the design."
    )
    parameters = {
        "type": "object",
        "required": ["stage", "input", "source"],
        "properties": {
            "stage": {"type": "string"},
            "input": {"type": "string", "description": "the stage's input name (kb_lookup lists them)"},
            "source": {"type": "string", "description": "user:<role> or stage:<name>.<output>"},
        },
    }

    def __init__(self, context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            ctx = self._context()
            with ctx.store.locked():  # one change to this chat's pipeline at a time
                pipeline = ctx.store.load()
                if pipeline is None:
                    return ToolResult.text("no pipeline in this chat yet — pipeline_plan first", is_error=True)
                stage = pipeline.stage(str(params.get("stage") or ""))
                if stage is None:
                    return ToolResult.text(f"no stage '{params.get('stage')}'", is_error=True)
                name, source = str(params.get("input") or "").strip(), str(params.get("source") or "").strip()
                stage.inputs = [i for i in stage.inputs if i.name != name] + [StageInput(name, source)]
                order = pipeline.order_problems()
                if order:
                    return ToolResult.text("\n".join(order), is_error=True)
                try:
                    ctx.store.save(pipeline, only={stage.name})
                except StageBuildError as e:
                    return ToolResult.text(str(e), is_error=True)
                stale = PipelineRunRecord(ctx.workspace).stale_from(pipeline, stage.name)
                report = ctx.validate(pipeline)
                return ToolResult.text(f"stage {stage.name}.{name} <- {source}\n" + render_report(report)
                                       + PipelineRunRecord.describe_stale(stale),
                                       details={"holds": report.holds}, is_error=not report.holds)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"stage_bind failed: {type(e).__name__}: {e}", is_error=True)


__all__ = ["StageBindTool"]
