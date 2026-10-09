"""StagePicks — which of a stage's results the person picked to go on: `stages/stage_picks.json`.

THE WINDOW'S FILE. The Stages panel writes it (workspace.upload, overwrite) when the person clicks
a result — instant and free, no agent turn, the way Ad Studio's step pick is. The run reads it to
hand a stage's PICKED result to the stages after it; nothing picked means the latest.

    {"<stage>": "<workspace-relative path of the picked file>"}

A pick is a preference, never a gate: a pick that is not one of the stage's own results (an old
file, a typo) is ignored and the latest is used — the run checks it against the run record.
"""

from __future__ import annotations

import json
from pathlib import Path

import chat_paths

FILE = "stage_picks.json"


class StagePicks:
    def __init__(self, workspace: Path) -> None:
        path = Path(workspace) / chat_paths.chat_rel(chat_paths.WORKFLOWS) / chat_paths.STAGES / FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self._picks = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def picked(self, stage: str) -> str:
        return self._picks.get(stage, "")


__all__ = ["FILE", "StagePicks"]
