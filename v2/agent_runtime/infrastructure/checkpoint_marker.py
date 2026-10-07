"""The checkpoint stamp — one JSON file in the run's workspace, written by the daemon, read by
the tools that spend money.

TWO MOMENTS. `present()` the moment a checkpoint TOOL returns (with what it showed) — `ask_user` (plugins/ask), which
declares `checkpoint = True` and which the gateway stamps on its tool_execution_end; `answer()`
when the next user message arrives on that session. A tool that wants to run a workflow asks: was a checkpoint presented after this
workflow first existed, and has it been answered since? Both answers live here.

IN THE WORKSPACE, because that is the one place the daemon and a sandboxed plugin both reach:
the same `.studio/` folder the GPU handover and the studio telemetry already use. PER SESSION
inside the file, because the workspace is shared across every conversation of the account.

BEST-EFFORT, LIKE EVERY WRITE IN `.studio/`. A stamp that cannot be written costs the user a
refused run with a clear message, never a broken turn.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_FILE = ".studio/checkpoint.json"
_MAX_SESSIONS = 200


def _path(workspace: str) -> Path:
    return Path(workspace) / _FILE


def _load(workspace: str) -> dict:
    try:
        data = json.loads(_path(workspace).read_text(encoding="utf-8"))
        sessions = data.get("sessions") if isinstance(data, dict) else None
        return sessions if isinstance(sessions, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(workspace: str, sessions: dict) -> None:
    if len(sessions) > _MAX_SESSIONS:
        sessions = dict(list(sessions.items())[-_MAX_SESSIONS:])
    p = _path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"sessions": sessions}, indent=1), encoding="utf-8")


def present(workspace: str, session_key: str, run_id: str = "", presented: dict | None = None) -> None:
    """A checkpoint tool just returned on `session_key`. Resets any earlier answer: a new
    checkpoint is a new question. `presented` is what the tool showed (its structured result), kept
    so the tools that act on the answer can compare it with what they are about to do."""
    if not workspace or not session_key:
        return
    sessions = _load(workspace)
    sessions[session_key] = {"presented_at": time.time(), "answered_at": 0.0, "run_id": run_id,
                             "presented": presented or {}}
    _save(workspace, sessions)


def answer(workspace: str, session_key: str, text: str = "") -> bool:
    """A user message arrived on `session_key`. Counts as the answer to a presented, unanswered
    checkpoint — and only then. Returns whether it did. `text` is what they said (kept, bounded):
    an answer is not always a yes — "Instead: …" asks for a different design, and the tool that
    acts on the answer has to be able to see that."""
    if not workspace or not session_key:
        return False
    sessions = _load(workspace)
    rec = sessions.get(session_key)
    if not isinstance(rec, dict) or not rec.get("presented_at"):
        return False
    if float(rec.get("answered_at") or 0.0) >= float(rec.get("presented_at") or 0.0):
        return False
    rec["answered_at"] = time.time()
    rec["answer"] = str(text or "")[:4000]
    sessions[session_key] = rec
    _save(workspace, sessions)
    return True


def read(workspace: str, session_key: str) -> dict:
    return dict(_load(workspace).get(session_key) or {})
