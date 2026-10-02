"""PipelineRunRecord — what each stage of this chat's pipeline has done: `workflows/<chat>/pipeline_runs.json`.

Per stage: its status, the prompt it is rendering (a video outlives one tool call), and the files it
produced, by output name. The run reads it to hand a stage's output to the stages after it; the
agent reads it (pipeline_status) to say where the job is. Re-running a stage marks every stage
after it STALE — their inputs are about to change — so nothing downstream is shown as current.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import chat_paths
from pipeline import Pipeline

FILE = "pipeline_runs.json"
DONE, RENDERING, FAILED, STALE = "done", "rendering", "failed", "stale"


class PipelineRunRecord:
    def __init__(self, workspace: Path) -> None:
        self._path = Path(workspace) / chat_paths.chat_rel(chat_paths.WORKFLOWS) / FILE
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def stage(self, name: str) -> dict:
        return dict(self._data.get(name) or {})

    def status(self, name: str) -> str:
        return str((self._data.get(name) or {}).get("status") or "")

    def output_files(self, name: str, output: str) -> list[str]:
        return list(((self._data.get(name) or {}).get("outputs") or {}).get(output) or [])

    def rendering(self, name: str, prompt_id: str) -> None:
        self._set(name, {"status": RENDERING, "prompt_id": prompt_id, "outputs": {}})

    def done(self, name: str, outputs: dict[str, list[str]]) -> None:
        self._set(name, {"status": DONE, "prompt_id": "", "outputs": outputs})

    def failed(self, name: str, why: str) -> None:
        self._set(name, {"status": FAILED, "prompt_id": "", "outputs": {}, "why": why[:500]})

    def stale_after(self, pipeline: Pipeline, name: str) -> list[str]:
        """Mark every stage after `name` stale; returns their names."""
        names = [s.name for s in pipeline.stages]
        later = names[names.index(name) + 1:] if name in names else []
        for n in later:
            if n in self._data:
                self._data[n]["status"] = STALE
        self._save()
        return later

    def next_to_run(self, pipeline: Pipeline) -> str | None:
        for s in pipeline.stages:
            if self.status(s.name) != DONE:
                return s.name
        return None

    def _set(self, name: str, entry: dict) -> None:
        entry["at"] = time.time()
        self._data[name] = entry
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=1), encoding="utf-8")


__all__ = ["DONE", "FAILED", "RENDERING", "STALE", "PipelineRunRecord"]
