"""PipelineStore — a chat's pipeline on disk: `workflows/<chat>/pipeline.json` plus one workflow per stage.

The pipeline file holds the design's DECISIONS (which recipe, which port values, which input reads
what); each stage's graph is an ordinary workflow beside it (`<stage>.api.json` + `<stage>.json`,
written by WorkflowFileWriter), so everything that runs, lists, validates or saves a workflow treats
a stage as one. A custom stage's graph is read back from its own `.api.json` — that file IS its
design. Stage roles are recorded with their slot descriptions so the References panel says what
each one is for.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

import chat_paths
import reference_slots
from pipeline import Pipeline, Stage
from stage_builder import StageBuilder
from workflow_file_writer import WorkflowFileWriter, WrittenWorkflow

PIPELINE_FILE = "pipeline.json"


class PipelineStore:
    def __init__(self, workspace: Path, builder: StageBuilder, writer: WorkflowFileWriter) -> None:
        self._ws = Path(workspace)
        self._builder = builder
        self._writer = writer

    @property
    def folder(self) -> Path:
        return self._ws / chat_paths.chat_rel(chat_paths.WORKFLOWS)

    def rel(self, name: str) -> str:
        return f"{chat_paths.chat_rel(chat_paths.WORKFLOWS)}/{name}"

    def load(self) -> Pipeline | None:
        p = self.folder / PIPELINE_FILE
        if not p.is_file():
            return None
        return Pipeline.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def graph(self, stage: Stage) -> dict:
        """The stage's current graph, as written."""
        p = self.folder / f"{stage.name}.api.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def save(self, pipeline: Pipeline, custom_graphs: dict[str, dict] | None = None,
             only: set[str] | None = None) -> dict[str, WrittenWorkflow]:
        """Write pipeline.json and (re)build the stages named in `only` (all when None).
        `custom_graphs`: the graph of each custom stage, by name, when it is new or changed."""
        custom_graphs = custom_graphs or {}
        written = {}
        if only is None:
            self._drop_stages_not_in(pipeline)
        for stage in pipeline.stages:
            if only is not None and stage.name not in only:
                continue
            base = custom_graphs.get(stage.name) or (self.graph(stage) if stage.custom else None)
            graph = self._builder.build(stage, base)
            whats = {i.role: self._what(pipeline, stage, i) for i in stage.inputs}
            fed_by = {i.role: f"{i.producer[0]}.{i.producer[1]}" for i in stage.inputs if i.producer}
            written[stage.name] = self._writer.write(stage.name, graph, whats, fed_by=fed_by)
        self.folder.mkdir(parents=True, exist_ok=True)
        # ATOMIC: a reader (or a parallel call) never sees half a file.
        tmp = self.folder / f"{PIPELINE_FILE}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(pipeline.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.folder / PIPELINE_FILE)
        return written

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

    def _drop_stages_not_in(self, pipeline: Pipeline) -> None:
        """A REPLACED DESIGN LEAVES NO STAGES BEHIND. The previous pipeline's stages that the new
        one does not have lose their workflow files and their slot records — otherwise the
        References panel keeps asking for inputs of a stage that no longer exists."""
        old = self.load()
        if old is None:
            return
        keep = {s.name for s in pipeline.stages}
        for stage in old.stages:
            if stage.name in keep:
                continue
            for suffix in (".api.json", ".json"):
                (self.folder / f"{stage.name}{suffix}").unlink(missing_ok=True)
            reference_slots.record(self._ws, stage.name, [], {})

    @staticmethod
    def _what(pipeline: Pipeline, stage: Stage, inp) -> str:
        prod = inp.producer
        if prod:
            return f"filled by stage '{prod[0]}' ({prod[1]}) when it has run"
        return f"for stage '{stage.name}' ({inp.name})"


__all__ = ["PIPELINE_FILE", "PipelineStore"]
