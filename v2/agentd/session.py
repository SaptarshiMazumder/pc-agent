"""JSONL session transcript store.

File format (one JSON object per line), mirroring the reference jsonl storage:
  {"type":"session","version":3,"id":...,"timestamp":...,"cwd":...}
  {"type":"message","id":...,"parentId":...,"timestamp":...,"message":{...}}

Linear chain only (no branching/compaction): parentId is the previous line's id.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .types import Message, message_from_dict, message_to_dict


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, state_dir: Path, session_id: str, cwd: str = ""):
        self.session_id = session_id
        self.cwd = cwd
        self.path = Path(state_dir) / "sessions" / f"{session_id}.jsonl"
        self._last_id: str | None = None

    def load(self) -> list[Message]:
        """Replay the transcript; create the file with a header line if new."""
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            header = {
                "type": "session",
                "version": 3,
                "id": self.session_id,
                "timestamp": _iso_now(),
                "cwd": self.cwd,
            }
            self.path.write_text(json.dumps(header) + "\n", encoding="utf-8")
            return []

        messages: list[Message] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("type") == "message":
                    messages.append(message_from_dict(entry["message"]))
                    self._last_id = entry.get("id")
        return messages

    def append(self, message: Message) -> str:
        """Append one message line; returns the new entry id."""
        entry_id = uuid.uuid4().hex[:12]
        entry = {
            "type": "message",
            "id": entry_id,
            "parentId": self._last_id,
            "timestamp": _iso_now(),
            "message": message_to_dict(message),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._last_id = entry_id
        return entry_id


def list_sessions(state_dir: Path) -> list[dict]:
    sessions_dir = Path(state_dir) / "sessions"
    if not sessions_dir.exists():
        return []
    out = []
    for p in sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with p.open("r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
        except OSError:
            line_count = 0
        out.append(
            {
                "sessionId": p.stem,
                "messages": max(0, line_count - 1),  # minus header
                "modified": p.stat().st_mtime,
            }
        )
    return out
