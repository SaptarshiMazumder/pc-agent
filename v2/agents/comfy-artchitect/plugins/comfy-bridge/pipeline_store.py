"""PipelineStore — a chat's pipeline on disk.

    workflows/<chat>/stages/pipeline.json     the design's DECISIONS (recipe, port values, wiring)
    workflows/<chat>/<design>.json / .api.json ONE workflow with every stage in it, wired — the
                                               file a person downloads (PipelineWorkflowAssembler)
    workflows/<chat>/stages/<stage>.api.json   each stage alone: what the agent runs, one at a time
                                               (review points, re-running one stage)

Each stage's graph is an ordinary workflow (written by WorkflowFileWriter), so everything that
runs, validates or saves a workflow treats a stage as one. A custom stage's graph is read back
from its own `.api.json` — that file IS its design. Stage roles are recorded with their slot
descriptions so the References panel says what each one is for.

ONE FILE, NOT ONE PER STAGE. A five-stage design used to leave ten workflow files in the chat;
the person wants one to drag into ComfyUI. The stages live one folder down, out of the list.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

import chat_paths
import reference_slots
from pipeline import Pipeline, Stage
from pipeline_workflow_assembler import PipelineWorkflowAssembler
from stage_builder import StageBuilder
from stage_plan_view import StagePlanView
from workflow_file_writer import WorkflowFileWriter, WrittenWorkflow

PIPELINE_FILE = "pipeline.json"
STAGES = chat_paths.STAGES


class PipelineStore:
    def __init__(self, workspace: Path, builder: StageBuilder, writer: WorkflowFileWriter,
                 assembler: PipelineWorkflowAssembler, view: StagePlanView) -> None:
        self._ws = Path(workspace)
        self._builder = builder
        self._writer = writer
        self._assembler = assembler
        self._view = view

    @property
    def folder(self) -> Path:
        return self._ws / chat_paths.chat_rel(chat_paths.WORKFLOWS)

    def rel(self, name: str) -> str:
        return f"{chat_paths.chat_rel(chat_paths.WORKFLOWS)}/{name}"

    def stage_rel(self, stage_name: str) -> str:
        """Workspace-relative path of a stage's run file."""
        return self.rel(f"{STAGES}/{stage_name}.api.json")

    def design_rel(self, pipeline: Pipeline) -> str:
        """Workspace-relative path of the ONE combined workflow's run file."""
        return self.rel(f"{self.design_name(pipeline)}.api.json")

    def load(self) -> Pipeline | None:
        p = self.folder / STAGES / PIPELINE_FILE
        if not p.is_file():
            p = self.folder / PIPELINE_FILE  # a chat from before it moved into stages/
        if not p.is_file():
            return None
        return Pipeline.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def graph(self, stage: Stage) -> dict:
        """The stage's current graph, as written."""
        p = self.folder / STAGES / f"{stage.name}.api.json"
        if not p.is_file():
            # A pipeline written before stages moved into their own folder.
            p = self.folder / f"{stage.name}.api.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def save(self, pipeline: Pipeline, custom_graphs: dict[str, dict] | None = None,
             only: set[str] | None = None) -> dict[str, WrittenWorkflow]:
        """Write pipeline.json and (re)build the stages named in `only` (all when None).
        `custom_graphs`: the graph of each custom stage, by name, when it is new or changed. The one
        combined workflow is write_design's — a side product, never what decides a design."""
        custom_graphs = custom_graphs or {}
        written = {}
        old = self.load()
        if only is None:
            self._drop_stages_not_in(old, pipeline)
        for stage in pipeline.stages:
            if only is not None and stage.name not in only:
                continue
            whats = {i.role: self._what(pipeline, stage, i) for i in stage.inputs}
            fed_by = {i.role: f"{i.producer[0]}.{i.producer[1]}" for i in stage.inputs if i.producer}
            if stage.seedream:
                # NO GRAPH: the provider makes it. Its inputs are still slots — the Inputs tab lists
                # them and the run reads them — so they are recorded as a workflow's would be.
                for folder in (self.folder / STAGES, self.folder):
                    (folder / f"{stage.name}.api.json").unlink(missing_ok=True)
                reference_slots.record(self._ws, stage.name, [i.role for i in stage.inputs], whats, fed_by=fed_by)
                continue
            base = custom_graphs.get(stage.name) or (self.graph(stage) if stage.custom else None)
            graph = self._builder.build(stage, base)
            written[stage.name] = self._writer.write(stage.name, graph, whats, fed_by=fed_by, subfolder=STAGES)
        self._drop_old_design(old, pipeline)
        # IN stages/, NOT BESIDE THE WORKFLOW: the window lists every .json in the chat's folder as
        # a ComfyUI file, and the design's bookkeeping showed up as two extra "workflows".
        stages = self.folder / STAGES
        stages.mkdir(parents=True, exist_ok=True)
        # ATOMIC: a reader (or a parallel call) never sees half a file.
        tmp = stages / f"{PIPELINE_FILE}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(pipeline.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, stages / PIPELINE_FILE)
        (self.folder / PIPELINE_FILE).unlink(missing_ok=True)  # where a chat from before kept it
        # THE STAGES PANEL'S VIEW follows every save, so what it shows is the design that runs.
        self._view.write(stages, pipeline, {s.name: self.graph(s) for s in pipeline.stages})
        return written

    @staticmethod
    def design_name(pipeline: Pipeline) -> str:
        """The combined workflow's file name: the design's name in file-safe words, never a stage's."""
        slug = re.sub(r"[^a-z0-9]+", "_", (pipeline.name or "").lower()).strip("_")[:48] or "pipeline"
        return f"{slug}_pipeline" if pipeline.stage(slug) or slug == "pipeline" else slug

    @contextmanager
    def locked(self, timeout_s: float = 60.0, stale_s: float = 300.0):
        """One change to this chat's pipeline at a time. Every design tool runs in its own process and
        does load -> change -> save; two at once (parallel tool calls) interleaved their writes and
        corrupted pipeline.json, or one silently dropped the other's change."""
        self.folder.mkdir(parents=True, exist_ok=True)
        lock = self.folder / ".pipeline.lock"
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > stale_s:
                        lock.unlink(missing_ok=True)  # a holder that died long ago
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError("another change to this pipeline is still being written - try again")
                time.sleep(0.1)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    def write_design(self, pipeline: Pipeline) -> str:
        """The ONE workflow the person downloads: every stage's current graph, joined and wired.
        A SIDE PRODUCT: the stage files and the design check are the design. Joining them once
        crashed on a case nobody had built (two inputs reading one output) and took the whole
        design call down with it — so a failure here is returned as one line for the tool's result
        (the download is missing, the design stands), never raised into the design tools."""
        try:
            assembled = self._assembler.assemble(pipeline, {s.name: self.graph(s) for s in pipeline.stages})
            self._writer.write_files(chat_paths.chat_rel(chat_paths.WORKFLOWS), self.design_name(pipeline),
                                     assembled.graph, groups=assembled.groups)
        except Exception as e:  # noqa: BLE001 — said in the result, see the docstring
            return (f"the one-file download ({self.design_rel(pipeline)}) was not written: {type(e).__name__}: "
                    f"{e}. The design and its stage files stand — go on; mention that this one file is missing.")
        return ""

    def _drop_old_design(self, old: Pipeline | None, pipeline: Pipeline) -> None:
        """A renamed design takes its old one-file workflow with it."""
        if old is not None and self.design_name(old) != self.design_name(pipeline):
            for suffix in (".api.json", ".json"):
                (self.folder / f"{self.design_name(old)}{suffix}").unlink(missing_ok=True)

    def _drop_stages_not_in(self, old: Pipeline | None, pipeline: Pipeline) -> None:
        """A REPLACED DESIGN LEAVES NO STAGES BEHIND. The previous pipeline's stages that the new
        one does not have lose their workflow files and their slot records — otherwise the
        References panel keeps asking for inputs of a stage that no longer exists."""
        if old is None:
            return
        keep = {s.name for s in pipeline.stages}
        for stage in old.stages:
            if stage.name in keep:
                continue
            for folder in (self.folder / STAGES, self.folder):
                for suffix in (".api.json", ".json"):
                    (folder / f"{stage.name}{suffix}").unlink(missing_ok=True)
            reference_slots.record(self._ws, stage.name, [], {})

    @staticmethod
    def _what(pipeline: Pipeline, stage: Stage, inp) -> str:
        prod = inp.producer
        if prod:
            return f"filled by stage '{prod[0]}' ({prod[1]}) when it has run"
        return f"for stage '{stage.name}' ({inp.name})"


__all__ = ["PIPELINE_FILE", "STAGES", "PipelineStore"]
