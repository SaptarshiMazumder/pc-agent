"""StagePlanView — the design as the Stages panel shows it: `workflows/<chat>/stages/stage_plan.json`.

WHY A FILE. The window reads a chat's state off disk through /file — the slot record
(`references/<chat>/.slots.json`) and the run record (`stages/pipeline_runs.json`) already work
this way. What a stage WILL do (its model, size, length, inputs, prompt) needs the knowledge base
to read its graph, which only the plugin has; so the plugin writes it here every time the design
is saved, and the panel reads it like any other record. A window click never has to run a tool
inside a chat to find out what the chat holds.

    {"name": "...", "stages": [{"name", "note", "facts": StageFactReader's}]}

What each stage has MADE is not here: that is the run record's, and the person's pick of a
result is the window's own file (stage_picks.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pipeline import Pipeline
from stage_fact_reader import StageFactReader

FILE = "stage_plan.json"


class StagePlanView:
    def __init__(self, reader: StageFactReader) -> None:
        self._reader = reader

    def write(self, folder: Path, pipeline: Pipeline, graphs: dict[str, dict]) -> None:
        """`folder`: the chat's stages folder."""
        facts = self._reader.facts(pipeline, graphs)
        view = {
            "name": pipeline.name,
            "stages": [{"name": s.name, "note": s.note, "facts": facts[s.name]} for s in pipeline.stages],
        }
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{FILE}.{os.getpid()}.tmp"  # ATOMIC: the panel never reads half a file
        tmp.write_text(json.dumps(view, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, folder / FILE)


__all__ = ["FILE", "StagePlanView"]
