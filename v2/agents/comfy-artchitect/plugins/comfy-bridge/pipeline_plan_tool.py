"""pipeline_plan — lay out the job as stages, build each one, and check the whole design.

Phase 1 of the protocol: the design, with no GPU. Each stage is a knowledge-base recipe (family +
recipe + the port values to change + where each media input comes from) or, when no recipe covers
the model, a graph written node by node (`nodes` + `outputs`). This writes every stage as an
ordinary workflow (stages/ in the chat's folder), then validates the whole pipeline and returns the report.
Replaces the chat's pipeline when one exists.
"""

from __future__ import annotations

import re

from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from pipeline import Pipeline, Stage, StageInput
from pipeline_run_record import PipelineRunRecord
from pipeline_tool_context import PipelineToolContext
from pipeline_validator import render_report
from stage_builder import StageBuildError
from stage_lora import LORAS_SCHEMA, StageLora
from workflow_link import WorkflowLink


class PipelinePlanTool(Tool):
    name = "pipeline_plan"
    label = "Design the pipeline"
    default_retryable = False
    description = (
        "Design the job as 1..N stages and check it — no GPU needed. Each stage: `name`, `family` "
        "and `recipe` from kb_lookup, `ports` to change (prompt, size, length, seed, …), `inputs` "
        "binding each media input to `user:<role>` (a file the person adds) or "
        "`stage:<earlier stage>.<output>` (what an earlier stage made), and `review: true` to stop "
        "and show its output before later stages run (put it before anything slow, like video). "
        "`loras` puts LoRAs on a recipe stage's model (a style, a character, an effect) — lora_search "
        "finds ones trained for that model. "
        "A model no recipe covers: give `nodes` (API format) and `outputs` instead of a recipe. "
        "Returns the validation report; fix with stage_set / stage_bind / stage_edit_graph until it "
        "holds."
    )
    parameters = {
        "type": "object",
        "required": ["name", "stages"],
        "properties": {
            "name": {"type": "string", "description": "What the job is, in a few plain words the person reads on the card (e.g. 'gym deadlift reel') — not an identifier."},
            "deliver_size": {"type": "string", "description": "WIDTHxHEIGHT the person needs the final result at, when they named a size or a platform (4K = 3840x2160, a vertical reel = 1080x1920, 1080p = 1920x1080). The check compares the last stage with it."},
            "stages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "note"],
                    "properties": {
                        "name": {"type": "string", "description": "lowercase_with_underscores, e.g. character_sheet"},
                        "family": {"type": "string"},
                        "recipe": {"type": "string"},
                        "ports": {"type": "object", "description": "{port: value} — only what differs from the recipe"},
                        "inputs": {"type": "object", "description": "{input: 'user:<role>' | 'stage:<name>.<output>'}"},
                        "loras": LORAS_SCHEMA,
                        "review": {"type": "boolean"},
                        "note": {"type": "string", "description":
                                 "What this step makes, in plain words for the person — no model, "
                                 "node or file names (e.g. 'a character sheet of you from your photo'). "
                                 "It is the step's line on the approval card."},
                        "nodes": {"type": "array", "description": "custom stage only: [{id, class_type, inputs}]"},
                        "outputs": {"type": "object", "description": "custom stage only: {name: {node, type}}"},
                    },
                },
            },
        },
    }

    def __init__(self, context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            ctx = self._context()
            with ctx.store.locked():  # one change to this chat's pipeline at a time
                stages, customs = [], {}
                for raw in params.get("stages") or []:
                    if not isinstance(raw, dict):
                        return ToolResult.text(f"not a stage object: {raw!r}", is_error=True)
                    try:
                        loras = StageLora.list_from(raw.get("loras"))
                    except ValueError as e:
                        return ToolResult.text(f"stage {raw.get('name') or '?'}: {e}", is_error=True)
                    stage = Stage(
                        name=str(raw.get("name") or "").strip(), family=str(raw.get("family") or "").strip(),
                        recipe=str(raw.get("recipe") or "").strip(), ports=dict(raw.get("ports") or {}),
                        inputs=[StageInput(str(k), str(v)) for k, v in (raw.get("inputs") or {}).items()],
                        outputs=dict(raw.get("outputs") or {}), review=bool(raw.get("review")),
                        note=str(raw.get("note") or ""), loras=loras,
                    )
                    if not stage.note.strip():
                        return ToolResult.text(f"stage {stage.name or '?'}: `note` is required — what this step "
                                               "makes, in plain words for the person", is_error=True)
                    if stage.custom:
                        graph, problem = _graph_from_nodes(raw.get("nodes"))
                        if problem:
                            return ToolResult.text(f"stage {stage.name}: {problem}", is_error=True)
                        if not stage.outputs:
                            return ToolResult.text(f"stage {stage.name}: a custom stage names its outputs "
                                                   "({name: {node, type}})", is_error=True)
                        customs[stage.name] = graph
                    stages.append(stage)
                deliver = str(params.get("deliver_size") or "").strip().lower().replace(" ", "")
                if deliver and not re.fullmatch(r"\d+x\d+", deliver):
                    return ToolResult.text(f"deliver_size '{deliver}' is WIDTHxHEIGHT, e.g. 3840x2160", is_error=True)
                pipeline = Pipeline(str(params.get("name") or "").strip(), stages, deliver_size=deliver)
                order = pipeline.order_problems()
                if order:
                    return ToolResult.text("the pipeline cannot be laid out:\n  " + "\n  ".join(order), is_error=True)
                try:
                    written = ctx.store.save(pipeline, customs)
                except StageBuildError as e:
                    return ToolResult.text(str(e), is_error=True)
                # A NEW DESIGN: nothing an earlier one rendered is its result.
                stale = PipelineRunRecord(ctx.workspace).stale_from(pipeline, pipeline.stages[0].name)
                report = ctx.validate(pipeline)
                files = [w.api_rel for w in written.values()]
                return ToolResult.text(
                    render_report(report) + "\nstage workflows: " + ", ".join(files)
                    + PipelineRunRecord.describe_stale(stale),
                    details={"holds": report.holds, "stages": [s.name for s in pipeline.stages]},
                    artifacts=files,
                    is_error=not report.holds,
                )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"pipeline_plan failed: {type(e).__name__}: {e}", is_error=True)


def _graph_from_nodes(nodes) -> tuple[dict, str]:
    if not isinstance(nodes, list) or not nodes:
        return {}, "a custom stage needs `nodes` (or give a family and recipe)"
    api: dict = {}
    for node in nodes:
        if not isinstance(node, dict):
            return {}, f"not a node object: {node!r}"
        nid, cls, inputs = str(node.get("id") or "").strip(), str(node.get("class_type") or "").strip(), node.get("inputs")
        if not nid or not cls or not isinstance(inputs, dict):
            return {}, f"node {nid or '?'} needs id, class_type and an inputs object"
        if nid in api:
            return {}, f"two nodes share id {nid}"
        api[nid] = {"class_type": cls, "inputs": dict(inputs)}
    for nid, entry in api.items():
        for field, value in entry["inputs"].items():
            try:
                link = WorkflowLink.from_input(value, api, normalize_node_id=True)
            except ValueError as exc:
                return {}, f"node {nid}.{field} {exc}"
            if link is not None:
                entry["inputs"][field] = link.as_input()
    return api, ""


__all__ = ["PipelinePlanTool"]
