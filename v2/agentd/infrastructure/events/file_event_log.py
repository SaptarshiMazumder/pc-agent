"""FileEventLog — append each run's events to ``<state_dir>/events/<agent>-<run>.jsonl``.

ONE file per run, named by agent + run id (so a viewer can find "the latest run for agent X").
Each line is ``{ts, sessionKey, runId, event}``. Handles are kept open per run and closed on the
run's ``agent_end``; the directory is pruned to the most recent N run files (never pruning a run
that's still open). Best-effort throughout — it NEVER raises into the caller, because losing an
event must never break a run.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from agentd.domain.agent import agent_id_from_session_key
from agentd.domain.events import AgentEvent


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s or "")


class FileEventLog:
    def __init__(self, events_dir, max_runs: int = 200):
        self._dir = Path(events_dir)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._max_runs = max(1, int(max_runs))
        self._handles: dict[str, object] = {}     # run_id -> open append handle

    def emit(self, session_key: str, run_id: str, event: AgentEvent) -> None:
        f = self._handle(session_key, run_id)
        if f is None:
            return
        try:
            f.write(json.dumps(
                {"ts": time.time(), "sessionKey": session_key, "runId": run_id,
                 "event": event.to_dict()}, ensure_ascii=False) + "\n")
            f.flush()                              # flush so a tailing viewer sees it live
        except (OSError, ValueError, TypeError):
            pass
        if event.type == "agent_end":             # run finished -> release the handle
            self._close_one(run_id)

    def _handle(self, session_key: str, run_id: str):
        h = self._handles.get(run_id)
        if h is not None:
            return h
        self._prune()
        agent = agent_id_from_session_key(session_key)
        path = self._dir / f"{_safe(agent)}-{_safe(run_id)}.jsonl"
        try:
            h = path.open("a", encoding="utf-8")
        except OSError:
            return None
        self._handles[run_id] = h
        return h

    def _prune(self) -> None:
        """Keep only the most recent N run files; never delete one that's still open."""
        try:
            files = sorted(self._dir.glob("*.jsonl"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        open_names = {Path(getattr(h, "name", "")).name for h in self._handles.values()}
        for p in files[self._max_runs:]:
            if p.name in open_names:
                continue
            try:
                p.unlink()
            except OSError:
                pass

    def _close_one(self, run_id: str) -> None:
        h = self._handles.pop(run_id, None)
        if h is not None:
            try:
                h.close()
            except OSError:
                pass

    def close(self) -> None:
        for h in self._handles.values():
            try:
                h.close()
            except OSError:
                pass
        self._handles.clear()


def build_event_log(config):
    """The EventSink for the composition root — None unless AGENTD_EVENT_LOG is on, so the
    whole observability layer is cut-out-able by a single flag."""
    if not getattr(config, "event_log_enabled", False):
        return None
    events_dir = Path(getattr(config, "state_dir", ".")) / "events"
    return FileEventLog(events_dir, max_runs=getattr(config, "event_log_max_runs", 200))
