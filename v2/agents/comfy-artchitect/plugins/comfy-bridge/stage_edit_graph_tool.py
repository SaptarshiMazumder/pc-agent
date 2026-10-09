"""stage_edit_graph — change a stage's wiring, one checked operation at a time.

For what ports cannot express: adding a LoRA, swapping a node, rewiring an input. The operations
(GraphEditOps) apply to the stage's current graph and each is checked as it applies; nothing is
written unless all of them did. A RECIPE stage that is rewired becomes a CUSTOM stage — its graph
is its own from then on, so its ports are gone (values change with `set_input`); the tool says so.
A VALUE IS NOT WIRING: `set_input` ops that land on inputs the recipe's ports set are applied as
those ports, and the stage keeps its recipe — a changed length once cost a stage its ports, its
LoRAs and its checks against the recipe for the rest of the design.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from graph_edit_ops import GraphEditError, GraphEditOps
from recipe import Recipe
from pipeline import StageInput
from pipeline_run_record import PipelineRunRecord
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

    @staticmethod
    def _as_ports(recipe: Recipe, ops: list) -> dict | None:
        """{port: value} when every op only sets a value a port of the recipe sets; else None."""
        ports = _port_inputs(recipe)
        out = {}
        for op in ops:
            key = (str((op or {}).get("node")), str((op or {}).get("input")))
            if (op or {}).get("op") != "set_input" or key not in ports:
                return None
            out[ports[key]] = op.get("value")
        return out or None

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
                if stage.seedream:
                    return ToolResult.text(f"stage {stage.name} is a Seedream image stage — it has no graph. Change "
                                           "its prompt, aspect_ratio or count with stage_set, its pictures with "
                                           "stage_bind.", is_error=True)
                as_ports = None if stage.custom else self._as_ports(ctx.builder.recipe_of(stage), params.get("ops") or [])
                if as_ports:
                    stage.ports.update(as_ports)
                    try:
                        ctx.store.save(pipeline, only={stage.name})
                    except StageBuildError as e:
                        return ToolResult.text(str(e), is_error=True)
                    stale = PipelineRunRecord(ctx.workspace).stale_from(pipeline, stage.name)
                    report = ctx.validate(pipeline)
                    design_note = ctx.store.write_design(pipeline)
                    return ToolResult.text(
                        f"stage {stage.name}: values set through its recipe's ports "
                        f"({', '.join(f'{k} = {v!r}' for k, v in as_ports.items())}) — it keeps its recipe\n"
                        + render_report(report) + (f"\n! {design_note}" if design_note else "")
                        + PipelineRunRecord.describe_stale(stale),
                        details={"holds": report.holds}, is_error=not report.holds)
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
                stale = PipelineRunRecord(ctx.workspace).stale_from(pipeline, stage.name)
                report = ctx.validate(pipeline)
                design_note = ctx.store.write_design(pipeline)
                return ToolResult.text("applied:\n  " + "\n  ".join(done) + note + "\n" + render_report(report)
                                       + (f"\n! {design_note}" if design_note else "")
                                       + PipelineRunRecord.describe_stale(stale),
                                       details={"holds": report.holds}, is_error=not report.holds)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"stage_edit_graph failed: {type(e).__name__}: {e}", is_error=True)


def _port_inputs(recipe: Recipe) -> dict[tuple[str, str], str]:
    """{(node id, input): port} — the inputs a recipe's ports set."""
    out = {}
    for name, spec in recipe.ports.items():
        for nid in spec.get("nodes") or ([spec["node"]] if spec.get("node") else []):
            out[(str(nid), str(spec.get("input")))] = name
    return out


__all__ = ["StageEditGraphTool"]
