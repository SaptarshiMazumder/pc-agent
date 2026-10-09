"""PipelineRunRecord — what each stage of this chat's pipeline has done: `workflows/<chat>/stages/pipeline_runs.json`.

Per stage: its status, the prompt it is rendering (a video outlives one tool call), and the files it
produced, by output name. The run reads it to hand a stage's output to the stages after it; the
agent reads it (pipeline_status) to say where the job is. Re-running a stage marks every stage
after it STALE — their inputs are about to change — and changing a stage's design marks it and every
stage after it STALE, so nothing that no longer matches the design is shown as current or handed on.
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
        folder = Path(workspace) / chat_paths.chat_rel(chat_paths.WORKFLOWS)
        # In stages/ with the stage files — not beside the workflow, where the window listed it
        # as one. `_legacy` is where a chat from before kept it: read once, moved on the next save.
        self._path = folder / chat_paths.STAGES / FILE
        self._legacy = folder / FILE
        source = self._path if self._path.is_file() else self._legacy
        try:
            self._data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def stage(self, name: str) -> dict:
        return dict(self._data.get(name) or {})

    def status(self, name: str) -> str:
        return str((self._data.get(name) or {}).get("status") or "")

    def output_files(self, name: str, output: str) -> list[str]:
        return list(((self._data.get(name) or {}).get("outputs") or {}).get(output) or [])

    def rendering(self, name: str, prompt_id: str, fed: dict[str, str] | None = None) -> None:
        self._set(name, {"status": RENDERING, "prompt_id": prompt_id, "outputs": {}, "fed": dict(fed or {})})

    def done(self, name: str, outputs: dict[str, list[str]], fed: dict[str, str] | None = None) -> None:
        """`fed`: {role: the file each input was given} — the Stages panel compares it with what is
        picked now, to say a stage's result was made from different inputs."""
        # EVERY RESULT IS KEPT, not just the last run's: the person picks which one goes on.
        results = [*((self._data.get(name) or {}).get("results") or []), {"at": time.time(), "outputs": outputs}]
        self._set(name, {"status": DONE, "prompt_id": "", "outputs": outputs, "fed": dict(fed or {}),
                         "results": results})

    def results(self, name: str, output: str) -> list[str]:
        """Every file this stage has made for `output`, oldest first."""
        return [f for r in (self._data.get(name) or {}).get("results") or [] for f in (r.get("outputs") or {}).get(output) or []]

    def failed(self, name: str, why: str) -> None:
        self._set(name, {"status": FAILED, "prompt_id": "", "outputs": {}, "why": why[:500]})

    def stale_after(self, pipeline: Pipeline, name: str) -> list[str]:
        """Mark every stage after `name` stale (`name` is about to run again); returns those marked."""
        return self._mark_stale(pipeline, name, inclusive=False)

    def stale_from(self, pipeline: Pipeline, name: str) -> list[str]:
        """`name`'s design changed: its result and every later stage's no longer match it. Returns
        the stages that had a result and are now stale."""
        return self._mark_stale(pipeline, name, inclusive=True)

    @staticmethod
    def describe_stale(marked: list[str]) -> str:
        """The line an edit tool adds when its change made earlier results stale; '' when none did."""
        if not marked:
            return ""
        return (f"\nno longer current (made before this change): {', '.join(marked)} — "
                "pipeline_run redoes them from the first.")

    def _mark_stale(self, pipeline: Pipeline, name: str, inclusive: bool) -> list[str]:
        names = [s.name for s in pipeline.stages]
        if name not in names:
            return []
        start = names.index(name) + (0 if inclusive else 1)
        marked = [n for n in names[start:] if n in self._data and self._data[n].get("status") != STALE]
        for n in marked:
            self._data[n]["status"] = STALE
        if marked:
            self._save()
        return marked

    def next_to_run(self, pipeline: Pipeline) -> str | None:
        for s in pipeline.stages:
            if self.status(s.name) != DONE:
                return s.name
        return None

    def _set(self, name: str, entry: dict) -> None:
        entry["at"] = time.time()
        # The results so far outlive a new run's status (rendering, failed).
        entry.setdefault("results", list((self._data.get(name) or {}).get("results") or []))
        self._data[name] = entry
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        self._legacy.unlink(missing_ok=True)


__all__ = ["DONE", "FAILED", "RENDERING", "STALE", "PipelineRunRecord"]
