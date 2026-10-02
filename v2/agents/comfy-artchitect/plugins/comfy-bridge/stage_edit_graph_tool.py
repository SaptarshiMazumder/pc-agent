"""stage_edit_graph — change a stage's wiring, one checked operation at a time.

For what ports cannot express: adding a LoRA, swapping a node, rewiring an input. The operations
(GraphEditOps) apply to the stage's current graph and each is checked as it applies; nothing is
written unless all of them did. A RECIPE stage that is rewired becomes a CUSTOM stage — its graph
is its own from then on, so its ports are gone (values change with `set_input`); the tool says so.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from graph_edit_ops import GraphEditError, GraphEditOps
from pipeline import StageInput
from pipeline_tool_context import PipelineToolContext
from pipeline_validator import render_report
from stage_builder import StageBuildError


class StageEditGraphTool(Tool):
    name = "stage_edit_graph"
    label = "Edit a stage's graph"
    default_retryable = False
    description = (
        "Edit a stage's graph with operations — set_input {node, input, value}; link {node, input, "
        "from, output}; unlink {node, input}; add_node {id, class_type, inputs}; remove_node {node} — "
        "each checked as it applies, then the whole design is re-checked. Use ports (stage_set) for "
        "settings; this is for wiring. A recipe stage you rewire becomes custom (no ports after)."
    )
    parameters = {
        "type": "object",
        "required": ["stage", "ops"],
        "properties": {
            "stage": {"type": "string"},
            "ops": {"type": "array", "items": {"type": "object"}},
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
                try:
                    edited, done = GraphEditOps(ctx.catalogue).apply(ctx.store.graph(stage), params.get("ops") or [])
                except GraphEditError as e:
                    return ToolResult.text(f"nothing changed — {e}", is_error=True)
                note = ""
                if not stage.custom:
                    # The graph is the stage's own from here: what the recipe exposed carries over.
                    recipe = ctx.builder.recipe_of(stage)
                    stage.outputs = dict(recipe.outputs)
                    stage.inputs = [StageInput(i.role, i.source) for i in stage.inputs]
                    stage.recipe, stage.ports = "", {}
                    note = (f"\nstage {stage.name} was rewired, so it is now a custom stage: its graph is its own, "
                            "its ports are gone — change values with set_input.")
                try:
                    ctx.store.save(pipeline, custom_graphs={stage.name: edited}, only={stage.name})
                except StageBuildError as e:
                    return ToolResult.text(str(e), is_error=True)
                report = ctx.validate(pipeline)
                return ToolResult.text("applied:\n  " + "\n  ".join(done) + note + "\n" + render_report(report),
                                       details={"holds": report.holds}, is_error=not report.holds)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"stage_edit_graph failed: {type(e).__name__}: {e}", is_error=True)


__all__ = ["StageEditGraphTool"]
