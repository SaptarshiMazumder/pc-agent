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
from datetime import UTC, datetime
from pathlib import Path

# Domain types + their (de)serialization. Imported from the canonical domain path
# now that this module lives in the infrastructure layer.
from agent_runtime.domain.messages import (
    AssistantMessage,
    Message,
    TextContent,
    ToolResultMessage,
    message_from_dict,
    message_to_dict,
)

#: What the model is told about a tool call whose result was never written. Deliberately states
#: the FACT (it did not finish) rather than inventing an outcome — an invented "succeeded" would
#: make the model build on work that never happened.
INTERRUPTED_TOOL_RESULT = (
    "This tool call did not finish — the run was interrupted (the daemon stopped or the "
    "connection dropped) before a result was recorded. Treat it as not run: if you still need "
    "it, call it again."
)


def close_unanswered_tool_calls(messages: list[Message]) -> list[Message]:
    """Give every tool call a result, so an interrupted run cannot brick a conversation.

    THE PROTOCOL INVARIANT. An assistant message carrying ``tool_calls`` must be followed by one
    tool message per ``tool_call_id`` — the model has to see the outcome of every call it made.
    Providers validate the WHOLE message array up front, so one unanswered call is rejected as a
    400 covering the entire request, not just the bad part.

    HOW A TRANSCRIPT ENDS UP LIKE THAT. The loop persists the assistant turn, runs the tools,
    then persists their results. Anything that stops the process in between — a daemon restart,
    a crash, a killed task — leaves the call written and the result not. Nothing then repairs it,
    so every later message replays the same malformed history and fails identically. Observed in
    the wild: a conversation with 97 healthy records answered eleven consecutive sends with
    "couldn't generate a response", and could never recover on its own.

    Repair happens ON LOAD and IN MEMORY. The file is left exactly as it is — it is the record of
    what happened, and an interrupted call IS what happened. Every existing broken session is
    fixed the next time it is opened, with no migration.
    """
    answered = {
        m.tool_call_id for m in messages if isinstance(m, ToolResultMessage) and m.tool_call_id
    }
    repaired: list[Message] = []
    for message in messages:
        repaired.append(message)
        if not isinstance(message, AssistantMessage):
            continue
        for call in message.tool_calls:
            if call.id and call.id not in answered:
                answered.add(call.id)
                repaired.append(
                    ToolResultMessage(
                        tool_call_id=call.id,
                        tool_name=getattr(call, "name", "") or "tool",
                        content=[TextContent(text=INTERRUPTED_TOOL_RESULT)],
                        is_error=True,
                    )
                )
    return repaired


def _iso_now() -> str:
    """UTC timestamp as an ISO-8601 string (used on each stored line)."""
    return datetime.now(UTC).isoformat()


def _safe_name(session_id: str) -> str:
    """The on-disk stem for a session key (':' etc. are illegal in Windows filenames)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id)


# --- session metadata (title, ...) — a small SIDECAR next to the transcript ---------
# Kept SEPARATE from the .jsonl on purpose: the transcript stays strictly append-only
# (no header rewrites, no races with an in-flight run), while a rename / auto-title is
# a tiny independent write. One JSON dict per session, extensible (title, manual, ...).


def _meta_path(state_dir: Path, session_id: str) -> Path:
    return Path(state_dir) / "sessions" / f"{_safe_name(session_id)}.meta.json"


def read_session_meta(state_dir: Path, session_id: str) -> dict:
    """The session's sidecar metadata ({} if none). Never raises."""
    try:
        return json.loads(_meta_path(state_dir, session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_session_meta(state_dir: Path, session_id: str, **fields) -> dict:
    """Merge fields into the session's sidecar (atomic write). Returns the new metadata."""
    path = _meta_path(state_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = read_session_meta(state_dir, session_id)
    meta.update(fields)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return meta


def _first_user_snippet(first_user: str, limit: int = 110) -> str:
    # generous: WIDE views (agent/project chat tables) show the full line; narrow lists
    # (sidebar) truncate visually with CSS ellipsis — display cropping is the client's job.
    collapsed = " ".join((first_user or "").split())
    return (collapsed[:limit].rstrip() + "…") if len(collapsed) > limit else collapsed


class SessionStore:
    """Reads/writes one session's transcript file (``<state_dir>/sessions/<id>.jsonl``)."""

    def __init__(self, state_dir: Path, session_id: str, cwd: str = ""):
        self.session_id = session_id  # the real logical key (kept in the header)
        self.cwd = cwd  # working dir recorded in the header (informational)
        # Agent session keys contain ':' (e.g. agent:<id>:<peer>) which is illegal in a
        # Windows filename — sanitize for the PATH only (keep the real key in the header).
        self.path = Path(state_dir) / "sessions" / f"{_safe_name(session_id)}.jsonl"
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
        return close_unanswered_tool_calls(messages)

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


def read_session_messages(state_dir: Path, session_id: str) -> list[dict]:
    """Read a stored session's transcript as message dicts (the camelCase wire form),
    WITHOUT creating the file — for a client loading a past conversation to display.

    Each dict additionally carries ``ts`` — the line's stored ISO timestamp — so any
    client can show WHEN a message was sent (the transcript always recorded it; this
    just stops dropping it on the way out). Returns [] if the session doesn't exist
    (never a side effect, unlike ``load`` which creates the header for a brand-new
    session). The id may be the real logical key or the sanitized filename stem
    (``list_sessions`` returns the stem) — both resolve to the same path via the
    store's own sanitization."""
    store = SessionStore(state_dir, session_id)
    if not store.path.exists():
        return []
    out: list[dict] = []
    with store.path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "message":
                continue
            message = dict(entry.get("message") or {})
            message["ts"] = entry.get("timestamp") or ""
            out.append(message)
    return out


def delete_session(state_dir: Path, session_id: str) -> bool:
    """Delete a session — its transcript AND its meta sidecar. True if a transcript
    was actually removed. Never raises (a vanished file is already 'deleted')."""
    store = SessionStore(state_dir, session_id)
    existed = store.path.exists()
    for path in (store.path, _meta_path(state_dir, session_id)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False
    return existed


def duplicate_session(state_dir: Path, session_id: str) -> str | None:
    """Copy a session's transcript + meta into a NEW session and return its id (== the
    filename stem, which is what a client resumes by), or None if the source is missing.

    The transcript is copied VERBATIM except the header line's logical ``id`` (and its
    timestamp), so the copy is an independent, self-consistent thread. The new id is
    generated filename-safe so its on-disk stem equals the id ``list_sessions`` reports.
    The copy keeps the source's project and gets a "… (copy)" manual title."""
    src = SessionStore(state_dir, session_id)
    if not src.path.exists():
        return None
    new_id = f"copy-{uuid.uuid4().hex}"  # already filename-safe => stem == id
    dst_path = Path(state_dir) / "sessions" / f"{new_id}.jsonl"

    lines = src.path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") == "session":  # rewrite the header's logical id
            entry["id"] = new_id
            entry["timestamp"] = _iso_now()
            lines[i] = json.dumps(entry)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    meta = read_session_meta(state_dir, session_id)
    base = (meta.get("title") or "").strip()
    write_session_meta(
        state_dir,
        new_id,
        title=(f"{base} (copy)"[:80] if base else ""),
        manual=bool(base),
        projectId=meta.get("projectId") or "",
    )
    return new_id


def sessions_in_project(state_dir: Path, project_id: str) -> list[str]:
    """Session ids (filename stems) under ONE state dir tagged with this project."""
    sessions_dir = Path(state_dir) / "sessions"
    if not project_id or not sessions_dir.exists():
        return []
    return [
        p.stem
        for p in sessions_dir.glob("*.jsonl")
        if read_session_meta(state_dir, p.stem).get("projectId") == project_id
    ]


def list_sessions(state_dir: Path) -> list[dict]:
    """List all stored sessions (newest first) with a message count and a display TITLE.

    The title is the stored sidecar title (a user rename or an auto-generated one), else
    a snippet of the first user message (so a brand-new chat still shows something
    readable, never the raw key), else "". One pass over each file counts lines AND grabs
    the first user message; the JSON parse stops once that snippet is found.
    """
    sessions_dir = Path(state_dir) / "sessions"
    if not sessions_dir.exists():
        return []
    out = []
    for p in sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        line_count = 0
        first_user = ""
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line_count += 1
                    if not first_user:  # cheap: only parse until found
                        try:
                            entry = json.loads(line)
                            msg = entry.get("message") if entry.get("type") == "message" else None
                            if msg and msg.get("role") == "user":
                                first_user = str(msg.get("content") or "")
                        except (ValueError, AttributeError):
                            pass
        except OSError:
            pass
        meta = read_session_meta(state_dir, p.stem)
        meta_title = meta.get("title")
        title = meta_title or _first_user_snippet(first_user)
        # a preview of the first user message for the WIDE chat tables (ChatGPT-style 2nd line).
        # Only when the chat has a REAL title (auto/manual): for an untitled chat the title already
        # IS the first message, so a snippet would just duplicate it — send "" and the client shows
        # one line.
        snippet = _first_user_snippet(first_user, 140) if meta_title else ""
        out.append(
            {
                "sessionId": p.stem,
                "title": title,
                "titleManual": bool(meta.get("manual")),
                "snippet": snippet,
                "projectId": meta.get("projectId") or "",  # "" => standalone chat
                "messages": max(0, line_count - 1),  # subtract the header line
                "modified": p.stat().st_mtime,
            }
        )
    return out
