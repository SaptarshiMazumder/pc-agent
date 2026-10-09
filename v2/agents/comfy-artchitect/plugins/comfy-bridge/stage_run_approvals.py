"""StageRunApprovals — a stage runs only when the person pressed Run on it: `.studio/stage_run_approvals.json`.

EVERY STAGE IS THE PERSON'S CALL, as every run is in Ad Studio. The approval card settles the
design; it does not let the agent run five stages on its own word. The Run button on a stage in the
Stages panel records a ONE-TIME approval for that chat and that stage, and pipeline_run must carry
its token: used up by the run it admits, refused without it.

Recording one is a WINDOW action (the approve tool checks the call came from a click), so a model
turn cannot approve its own run. The file is in `.studio/`, which every call is shipped with —
a click runs outside any chat, and this is where it can still write.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

FILE = ".studio/stage_run_approvals.json"
_KEEP = 20  # approvals older than the last few were never used


class StageNotApproved(Exception):
    pass


class StageRunApprovals:
    def __init__(self, workspace: Path, new_token: Callable[[], str]) -> None:
        self._path = Path(workspace) / FILE
        self._new_token = new_token

    def approve(self, chat: str, stage: str) -> str:
        """The person clicked Run: this stage of this chat may run, once. -> its token."""
        if not chat or not stage:
            raise ValueError("an approval names the chat and the stage")
        token = self._new_token()
        self._save([*self._load()[-(_KEEP - 1):], {"token": token, "chat": chat, "stage": stage, "at": time.time()}])
        return token

    def admit(self, chat: str, stage: str, token: str) -> None:
        """Let the run go ahead (the approval is used up), or raise StageNotApproved."""
        approvals = self._load()
        match = next((a for a in approvals if token and a.get("token") == token), None)
        if match is None or match.get("chat") != chat or match.get("stage") != stage:
            raise StageNotApproved(stage)
        self._save([a for a in approvals if a is not match])

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []

    def _save(self, approvals: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(approvals, indent=1), encoding="utf-8")
        os.replace(tmp, self._path)


__all__ = ["FILE", "StageNotApproved", "StageRunApprovals"]
