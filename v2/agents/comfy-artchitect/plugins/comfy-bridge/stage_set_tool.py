"""stage_set — change a stage's settings in place, and see at once whether the design still holds.

The edit is to PORTS (the recipe's named settings: prompt, negative, size, length, steps, seed,
camera move …), never to wiring: the stage is rebuilt from its recipe with the new values, written,
and the whole pipeline is validated again. A custom stage has no ports — use stage_edit_graph.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from pipeline_run_record import PipelineRunRecord
from pipeline_tool_context import PipelineToolContext
from pipeline_validator import render_report
from stage_builder import StageBuildError


class StageSetTool(Tool):
    name = "stage_set"
    label = "Change a stage's settings"
    default_retryable = False
    description = (
        "Set one or more ports of a pipeline stage (prompt, negative, width, height, length, steps, "
        "seed, …: kb_lookup shows a recipe's ports) and re-check the whole design. Only the values "
        "change; the stage's wiring stays the recipe's."
    )
    parameters = {
        "type": "object",
        "required": ["stage", "ports"],
        "properties": {
            "stage": {"type": "string"},
            "ports": {"type": "object", "description": "{port: value}"},
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
                    return ToolResult.text(f"no stage '{params.get('stage')}' (stages: "
                                           f"{', '.join(s.name for s in pipeline.stages)})", is_error=True)
                if stage.custom:
                    return ToolResult.text(f"stage {stage.name} has no recipe, so no ports — change it with "
                                           "stage_edit_graph set_input", is_error=True)
                new = dict(params.get("ports") or {})
                if not new:
                    return ToolResult.text("ports is empty", is_error=True)
                stage.ports.update(new)
                try:
                    ctx.store.save(pipeline, only={stage.name})
                except StageBuildError as e:
                    return ToolResult.text(str(e), is_error=True)
                stale = PipelineRunRecord(ctx.workspace).stale_from(pipeline, stage.name)
                report = ctx.validate(pipeline)
                return ToolResult.text(
                    f"stage {stage.name}: set {', '.join(new)}\n" + render_report(report)
                    + PipelineRunRecord.describe_stale(stale),
                    details={"holds": report.holds}, is_error=not report.holds,
                )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"stage_set failed: {type(e).__name__}: {e}", is_error=True)


__all__ = ["StageSetTool"]
