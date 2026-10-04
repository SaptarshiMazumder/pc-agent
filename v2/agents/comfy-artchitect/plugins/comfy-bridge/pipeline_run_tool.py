"""pipeline_run — Phase 3: run the next stage, bring its result back, hand it to the stages after it.

One stage per call. Before submitting, every input fed by an earlier stage gets that stage's output
file in its slot (references/<chat>/<role>.<ext>) — so comfy_run uploads it like any reference the
person added, and no filename is ever carried by hand. Then it runs (comfy_run; a long render is
collected by calling again, which uses comfy_run_status), downloads the outputs into the chat
(comfy_download, so they show), and records them by output name for the next stage.

At a REVIEW stage it stops: the person sees the result before anything slower spends their GPU
time. Re-running a stage (`stage` given, after a change) marks the stages after it stale.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import reference_slots
import studio_state
from pipeline import Pipeline, Stage
from pipeline_run_record import DONE, RENDERING, PipelineRunRecord
from pipeline_tool_context import PipelineToolContext

Run = Callable[[str, dict, object, object], Awaitable[ToolResult]]  # (workflow_path, fed, abort, on_update)
RunStatus = Callable[[str, object, object], Awaitable[ToolResult]]  # (prompt_id, abort, on_update)
Download = Callable[[list, object, object], Awaitable[ToolResult]]  # ([{filename, subfolder, type}], …)

_PROMPT_ID = re.compile(r"prompt[ _]id\s*'([0-9a-f-]{8,})'|\(prompt ([0-9a-f-]{8,})\)")


class PipelineRunTool(Tool):
    name = "pipeline_run"
    label = "Run the next step"
    default_retryable = False
    description = (
        "Run the pipeline's next stage (or the `stage` named — after a change, to redo it and what "
        "follows). It hands earlier stages' outputs to this one, renders, downloads the result into "
        "the chat and records it. A long render returns 'still rendering': call pipeline_run again "
        "to collect it. At a review stage, show the result and ask before running the next."
    )
    parameters = {
        "type": "object",
        "properties": {
            "stage": {"type": "string", "description": "Run this stage (again). Omit for the next one."},
        },
    }

    def __init__(self, run: Run, run_status: RunStatus, download: Download,
                 context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._run = run
        self._run_status = run_status
        self._download = download
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            ctx = self._context()
            pipeline = ctx.store.load()
            if pipeline is None:
                return ToolResult.text("no pipeline in this chat yet — pipeline_plan first", is_error=True)
            refused = studio_state.design_approved([s.name for s in pipeline.stages])
            if refused:
                return ToolResult.text(refused, is_error=True)
            record = PipelineRunRecord(ctx.workspace)
            asked = str(params.get("stage") or "").strip()
            name = asked or record.next_to_run(pipeline)
            if name is None:
                return ToolResult.text("every stage has run. Show the final result; a change is stage_set, "
                                       "then pipeline_run with that stage.")
            stage = pipeline.stage(name)
            if stage is None:
                return ToolResult.text(f"no stage '{name}'", is_error=True)

            pending = record.stage(name)
            if record.status(name) == RENDERING and pending.get("prompt_id") and not asked:
                res = await self._run_status(pending["prompt_id"], abort, on_update)
            else:
                problem, fed = self._hand_over(ctx, pipeline, stage, record)
                if problem:
                    return ToolResult.text(problem, is_error=True)
                if asked:
                    record.stale_after(pipeline, name)
                res = await self._run(ctx.store.stage_rel(stage.name), fed, abort, on_update)
            text = res.content[0].text if res.content else ""
            if res.is_error:
                record.failed(name, text)
                return ToolResult.text(f"stage {name} failed:\n{text}", is_error=True)
            if text.startswith("still rendering"):
                m = _PROMPT_ID.search(text)
                record.rendering(name, (m.group(1) or m.group(2)) if m else "")
                return ToolResult.text(f"stage {name} is still rendering — call pipeline_run again to collect it.")
            return await self._collect(ctx, pipeline, stage, record, res, abort, on_update)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"pipeline_run failed: {type(e).__name__}: {e}", is_error=True)

    # ------------------------------------------------------------------ hand-over

    @staticmethod
    def _hand_over(ctx: PipelineToolContext, pipeline: Pipeline, stage: Stage,
                   record: PipelineRunRecord) -> tuple[str, dict[str, str]]:
        """(problem or '', {role: the earlier stage's recorded output}). The run uploads the RECORDED
        file — the host downloaded it, so the host can send it. The copy into the slot folder is for
        the References panel only: on a microVM it is made inside the sandbox and reaches the host
        after the call, too late for an upload made during it."""
        folder = reference_slots.folder(ctx.workspace)
        fed: dict[str, str] = {}
        for inp in stage.inputs:
            prod = inp.producer
            if not prod:
                continue
            if record.status(prod[0]) != DONE:
                return f"stage {stage.name} needs {prod[0]}'s {prod[1]} — run {prod[0]} first (pipeline_run).", {}
            files = record.output_files(prod[0], prod[1])
            if not files:
                return f"stage {prod[0]} ran but produced no '{prod[1]}' — check its result before going on.", {}
            src = ctx.workspace / files[0]
            if not src.is_file():
                return f"{files[0]} (stage {prod[0]}'s {prod[1]}) is not in the workspace any more — run {prod[0]} again.", {}
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.iterdir():  # one file per role
                if old.is_file() and old.stem == inp.role:
                    old.unlink()
            shutil.copyfile(src, folder / f"{inp.role}{src.suffix.lower()}")
            fed[inp.role] = files[0]
        return "", fed

    # ------------------------------------------------------------------ collect

    async def _collect(self, ctx, pipeline: Pipeline, stage: Stage, record: PipelineRunRecord,
                       res: ToolResult, abort, on_update) -> ToolResult:
        entry = res.details if isinstance(res.details, dict) else {}
        produced = _files_by_node(entry.get("outputs") or {})
        outputs = stage.outputs if stage.custom else ctx.builder.recipe_of(stage).outputs
        wanted = [f for spec in outputs.values() for f in produced.get(str(spec.get("node")), [])]
        if not wanted:
            wanted = [f for fs in produced.values() for f in fs]
        if not wanted:
            record.failed(stage.name, "no output files")
            return ToolResult.text(f"stage {stage.name} ran but wrote no output files.", is_error=True)
        dl = await self._download(wanted, abort, on_update)
        if dl.is_error:
            return ToolResult.text(f"stage {stage.name} ran, but its outputs did not come back:\n"
                                   + dl.content[0].text, is_error=True)
        saved = list((dl.details or {}).get("saved") or [])
        by_name = {Path(p).name: p for p in saved}
        recorded = {
            out: [by_name[f["filename"]] for f in produced.get(str(spec.get("node")), []) if f["filename"] in by_name]
            for out, spec in outputs.items()
        }
        record.done(stage.name, recorded)
        nxt = record.next_to_run(pipeline)
        if stage.review:
            tail = (f"REVIEW POINT — show these (they are in the chat) and ask in one line whether they "
                    f"are right before {'the next step (' + nxt + ')' if nxt else 'finishing'}. A change: "
                    "stage_set, then pipeline_run with this stage.")
        elif nxt:
            tail = f"next: pipeline_run ({nxt})."
        else:
            tail = "that was the last stage — show the result and ask whether it is what they wanted."
        return ToolResult.text(f"stage {stage.name} done: " + ", ".join(saved) + "\n" + tail,
                               details={"stage": stage.name, "outputs": recorded}, artifacts=saved)


def _files_by_node(outputs: dict) -> dict[str, list[dict]]:
    """{node id: [{filename, subfolder, type}]} from a ComfyUI history entry's outputs."""
    out: dict[str, list[dict]] = {}
    for nid, node_out in (outputs or {}).items():
        if not isinstance(node_out, dict):
            continue
        for values in node_out.values():
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict) and item.get("filename"):
                        out.setdefault(str(nid), []).append({
                            "filename": str(item["filename"]), "subfolder": str(item.get("subfolder") or ""),
                            "type": str(item.get("type") or "output")})
    return out


__all__ = ["PipelineRunTool"]
