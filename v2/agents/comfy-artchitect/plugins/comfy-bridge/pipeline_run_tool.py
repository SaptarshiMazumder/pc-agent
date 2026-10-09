"""pipeline_run — Phase 3: run the next stage, bring its result back, hand it to the stages after it.

One stage per call. Before submitting, every input fed by an earlier stage gets that stage's output
file in its slot (references/<chat>/<role>.<ext>) — so comfy_run uploads it like any reference the
person added, and no filename is ever carried by hand. Then it runs (comfy_run; a long render is
collected by calling again, which uses comfy_run_status), downloads the outputs into the chat
(comfy_download, so they show), and records them by output name for the next stage.

At a REVIEW stage it stops: the person sees the result before anything slower spends their GPU
time. Re-running a stage (`stage` given, after a change) marks the stages after it stale.

EVERY STAGE RUNS ON THE PERSON'S CLICK: Run on the stage in the Stages panel records a one-time
approval, and the window sends the call carrying it (StageRunApprovals). An earlier stage's result
is handed on as the person PICKED it there (StagePicks); the latest when nothing is picked.
"""

from __future__ import annotations

import re
import secrets
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

import chat_paths
import reference_slots
import studio_state
from pipeline import Pipeline, Stage
from pipeline_run_record import DONE, RENDERING, PipelineRunRecord
from pipeline_tool_context import PipelineToolContext
from image_generation.budget_exceeded import BudgetExceeded
from image_generation.provider_refused import ProviderRefused
from image_generation.seedream_image_service import SeedreamImageService
from seedream_stage import SeedreamStage
from stage_picks import StagePicks
from stage_run_approvals import StageNotApproved, StageRunApprovals

Run = Callable[[str, dict, object, object], Awaitable[ToolResult]]  # (workflow_path, fed, abort, on_update)
RunStatus = Callable[[str, object, object], Awaitable[ToolResult]]  # (prompt_id, abort, on_update)
Download = Callable[[list, object, object], Awaitable[ToolResult]]  # ([{filename, subfolder, type}], …)

_PROMPT_ID = re.compile(r"prompt[ _]id\s*'([0-9a-f-]{8,})'|\(prompt ([0-9a-f-]{8,})\)")


class PipelineRunTool(Tool):
    name = "pipeline_run"
    label = "Run the next step"
    default_retryable = False
    # THE CALL BUDGET FOLLOWS THIS. The sandbox grants 32 requests per 120 s of declared time; one
    # call uploads, polls a render for up to 100 s (~23 polls) and downloads, and a long render
    # left the download without a request. 240 s doubles the budget; the poll still stops at 100 s.
    default_timeout_sec = 240.0
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
            "approval": {"type": "string",
                         "description": "The token from the person's Run click, exactly as the window sent it. "
                                        "Not needed to collect a stage that is already rendering."},
        },
    }

    def __init__(self, run: Run, run_status: RunStatus, download: Download,
                 context: Callable[[], PipelineToolContext] | None = None,
                 approvals: Callable[[Path], StageRunApprovals] | None = None,
                 picks: Callable[[Path], StagePicks] | None = None,
                 images: Callable[[], SeedreamImageService] | None = None) -> None:
        self._run = run
        self._run_status = run_status
        self._download = download
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))
        self._approvals = approvals or (lambda ws: StageRunApprovals(ws, lambda: secrets.token_hex(8)))
        self._picks = picks or StagePicks
        self._images = images  # Seedream stages; None = not set up (the run says so)

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
            # A STAGE THAT IS RENDERING IS COLLECTED, NEVER SUBMITTED AGAIN — however it is called.
            # "call pipeline_run again to collect it" was read as pipeline_run(stage=…), which this
            # took for "re-run": one H3 video was submitted five times, each billed on the
            # person's plan. A re-run of a rendering stage waits for its render to end.
            if record.status(name) == RENDERING:
                if not pending.get("prompt_id"):
                    return ToolResult.text(
                        f"stage {name} was submitted but its job id was not recorded, so it is not submitted "
                        "again: pipeline_status shows the job; when it has finished, pipeline_run brings it in.",
                        is_error=True)
                fed = dict(pending.get("fed") or {})
                res = await self._run_status(pending["prompt_id"], abort, on_update)
            else:
                problem, fed = self._hand_over(ctx, pipeline, stage, record, self._picks(ctx.workspace))
                if problem:
                    return ToolResult.text(problem, is_error=True)
                # THE PERSON PRESSES RUN ON EVERY STAGE (StageRunApprovals). Checked last before the
                # submit, so a stage that cannot run yet does not use up the click; collecting a render
                # already going needs no new click.
                try:
                    self._approvals(ctx.workspace).admit(chat_paths.chat_folder(), name,
                                                         str(params.get("approval") or "").strip())
                except StageNotApproved:
                    return ToolResult.text(
                        f"Nothing ran. Stage {name} runs when the person presses Run on it in the Stages "
                        "panel; the window then sends you pipeline_run with the approval. Tell them in one "
                        f"line that {name} is ready to run there — do not call pipeline_run again until they do.",
                        is_error=True)
                if asked:
                    record.stale_after(pipeline, name)
                if stage.seedream:
                    return self._seedream(ctx, pipeline, stage, record, fed)
                res = await self._run(ctx.store.stage_rel(stage.name), fed, abort, on_update)
            text = res.content[0].text if res.content else ""
            if res.is_error:
                record.failed(name, text)
                return ToolResult.text(f"stage {name} failed:\n{text}", is_error=True)
            if text.startswith("still rendering"):
                m = _PROMPT_ID.search(text)
                record.rendering(name, (m.group(1) or m.group(2)) if m else "", fed)
                return ToolResult.text(
                    f"stage {name} is still rendering (one job on Comfy Cloud). pipeline_run collects it — "
                    "calling it again only checks on this render, it never starts another.")
            return await self._collect(ctx, pipeline, stage, record, res, fed, abort, on_update)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"pipeline_run failed: {type(e).__name__}: {e}", is_error=True)

    # ------------------------------------------------------------------ hand-over

    @staticmethod
    def _hand_over(ctx: PipelineToolContext, pipeline: Pipeline, stage: Stage,
                   record: PipelineRunRecord, picks: StagePicks) -> tuple[str, dict[str, str]]:
        """(problem or '', {role: the earlier stage's recorded output}). The run uploads the RECORDED
        file — the host downloaded it, so the host can send it. The copy into the slot folder is for
        the References panel only: on a microVM it is made inside the sandbox and reaches the host
        after the call, too late for an upload made during it.

        WHICH OF ITS RESULTS: the one the person picked in the Stages panel, when it is one the
        stage made; otherwise the latest."""
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
            picked = picks.picked(prod[0])
            chosen = picked if picked in record.results(prod[0], prod[1]) else files[0]
            src = ctx.workspace / chosen
            if not src.is_file():
                return f"{chosen} (stage {prod[0]}'s {prod[1]}) is not in the workspace any more — run {prod[0]} again.", {}
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.iterdir():  # one file per role
                if old.is_file() and old.stem == inp.role:
                    old.unlink()
            shutil.copyfile(src, folder / f"{inp.role}{src.suffix.lower()}")
            fed[inp.role] = chosen
        return "", fed

    # ------------------------------------------------------------------ seedream

    def _seedream(self, ctx, pipeline: Pipeline, stage: Stage, record: PipelineRunRecord,
                  fed: dict[str, str]) -> ToolResult:
        """A Seedream stage: its pictures made by the provider, not Comfy Cloud — the references in
        the order they are bound (the person's files from their slots, earlier results as handed
        over), the credits checked before and charged after (SeedreamImageService)."""
        if self._images is None:
            return ToolResult.text("Seedream image making is not set up in this agent.", is_error=True)
        filled, missing = reference_slots.status(ctx.workspace, [i.role for i in stage.inputs if not i.producer])
        if missing:
            return ToolResult.text(f"stage {stage.name} is waiting for: {', '.join(missing)} — the person adds them "
                                   "on the Inputs tab. Say which, in one line, and end the turn.", is_error=True)
        references = [fed[i.role] if i.producer else filled[i.role] for i in stage.inputs]
        s = SeedreamStage(stage)
        out_stem = f"{chat_paths.chat_rel(chat_paths.OUTPUTS)}/{stage.name}-{int(time.time())}"
        try:
            made = self._images().generate(s.prompt, references, s.aspect_ratio, s.count, out_stem)
        except BudgetExceeded as e:
            record.failed(stage.name, str(e))
            return ToolResult.text(f"stage {stage.name} did not run — not enough credits: {e}. Tell the person; "
                                   "they can top up on the Credits page.", is_error=True)
        except ProviderRefused as e:
            record.failed(stage.name, str(e))
            return ToolResult.text(f"stage {stage.name}: {e}. The same pictures and prompt would be refused again — "
                                   "say why in one line and suggest what to change.", is_error=True)
        except Exception as e:  # noqa: BLE001 — said as it is
            record.failed(stage.name, f"{type(e).__name__}: {e}")
            return ToolResult.text(f"stage {stage.name} failed: {type(e).__name__}: {e}", is_error=True)
        paths = [m.path for m in made.media]
        record.done(stage.name, {"image": paths}, fed)
        nxt = record.next_to_run(pipeline)
        notes = "".join(f"\n  {n}" for n in made.notes)
        tail = (f"REVIEW POINT — show them and ask in one line whether they are right before "
                f"{'the next step (' + nxt + ')' if nxt else 'finishing'}." if stage.review else
                f"next: {nxt} — it runs when the person presses Run on it in the Stages panel; say it is ready."
                if nxt else "that was the last stage — show the result and ask whether it is what they wanted.")
        return ToolResult.text(
            f"stage {stage.name} done on Seedream 5 Pro ({made.backend.provider}): {', '.join(paths)} — "
            f"${made.cost_usd:.2f} of credits{notes}\n{tail}",
            details={"stage": stage.name, "outputs": {"image": paths}}, artifacts=paths)

    # ------------------------------------------------------------------ collect

    async def _collect(self, ctx, pipeline: Pipeline, stage: Stage, record: PipelineRunRecord,
                       res: ToolResult, fed: dict[str, str], abort, on_update) -> ToolResult:
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
            # THE RENDER IS KEPT. Recorded as nothing, the stage read as never run and the retry
            # submitted it again — a second FLUX.2 job, billed, for a file already rendered.
            # Kept as its job, the next call fetches the finished job and only downloads.
            record.rendering(stage.name, str(entry.get("prompt_id") or ""), fed)
            return ToolResult.text(f"stage {stage.name} rendered, but its outputs did not come back:\n"
                                   + dl.content[0].text + "\nThe render is kept: pipeline_run again "
                                   "downloads it, it does not render again.", is_error=True)
        saved = list((dl.details or {}).get("saved") or [])
        by_name = {Path(p).name: p for p in saved}
        recorded = {
            out: [by_name[f["filename"]] for f in produced.get(str(spec.get("node")), []) if f["filename"] in by_name]
            for out, spec in outputs.items()
        }
        record.done(stage.name, recorded, fed)
        nxt = record.next_to_run(pipeline)
        if stage.review:
            tail = (f"REVIEW POINT — show these (they are in the chat) and ask in one line whether they "
                    f"are right before {'the next step (' + nxt + ')' if nxt else 'finishing'}. A change: "
                    "stage_set; the person then presses Run on this stage again.")
        elif nxt:
            tail = f"next: {nxt} — it runs when the person presses Run on it in the Stages panel; say it is ready."
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
