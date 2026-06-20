"""Local JSONL session store — the on-disk memory backend.

This is an INFRASTRUCTURE implementation of memory storage (the future
``MemoryRepository`` interface). It persists a conversation as a JSONL file on the
local disk. It is the "build local-first" backend; later a cloud, end-to-end-
encrypted Memory Bank will be a sibling implementation behind the same interface,
swapped in by config — without the rest of the app changing.

File format (one JSON object per line), mirroring the reference jsonl storage:
  line 1 (header):  {"type":"session","version":3,"id":...,"timestamp":...,"cwd":...}
  then per message: {"type":"message","id":...,"parentId":...,"timestamp":...,"message":{...}}

It's a linear chain (no branching/compaction): each message's ``parentId`` points at
the previous line's id, so the file reads as a straight transcript. Loading = replay
the lines in order to rebuild the ``list[Message]`` the agent re-feeds to the LLM.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Domain types + their (de)serialization. Imported from the canonical domain path
# now that this module lives in the infrastructure layer.
from agentd.domain.messages import Message, message_from_dict, message_to_dict


def _iso_now() -> str:
    """UTC timestamp as an ISO-8601 string (used on each stored line)."""
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Reads/writes one session's transcript file (``<state_dir>/sessions/<id>.jsonl``)."""

    def __init__(self, state_dir: Path, session_id: str, cwd: str = ""):
        self.session_id = session_id  # the real logical key (kept in the header)
        self.cwd = cwd  # working dir recorded in the header (informational)
        # Agent session keys contain ':' (e.g. agent:<id>:<peer>) which is illegal in a
        # Windows filename — sanitize for the PATH only (keep the real key in the header).
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
        self.path = Path(state_dir) / "sessions" / f"{safe}.jsonl"
        self._last_id: str | None = None  # id of the last appended line (for parentId chaining)

    def load(self) -> list[Message]:
        """Replay the transcript into a list of messages.

        If the file is new, write the session header line and return an empty list.
        Otherwise read every ``message`` line in order and rebuild the Message objects
        (this is what gives the agent its memory across turns / restarts).
        """
        if not self.path.exists():
            # brand-new session: create the file with just the header line
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
                if entry.get("type") == "message":  # skip the header line
                    messages.append(message_from_dict(entry["message"]))
                    self._last_id = entry.get("id")  # remember the tail for chaining
        return messages

    def append(self, message: Message) -> str:
        """Append one message to the transcript; returns the new line's id.

        Each line links to the previous via ``parentId`` (a linear chain). The message
        itself is serialized with ``message_to_dict`` so it's plain JSON on disk.
        """
        entry_id = uuid.uuid4().hex[:12]
        entry = {
            "type": "message",
            "id": entry_id,
            "parentId": self._last_id,  # chain to the previous line
            "timestamp": _iso_now(),
            "message": message_to_dict(message),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._last_id = entry_id
        return entry_id


def list_sessions(state_dir: Path) -> list[dict]:
    """List all stored sessions (newest first) with a cheap message count.

    Used by the admin/sessions view. Counts lines minus the header; doesn't parse
    the messages, so it stays fast even with many/large transcripts.
    """
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
                "messages": max(0, line_count - 1),  # subtract the header line
                "modified": p.stat().st_mtime,
            }
        )
    return out
