"""The approval ledger the DAEMON keeps: `.studio/checkpoint.json` in the run's workspace.

The daemon sees the transcript, so it is the one that can say when an ask was shown and when the
user answered: it stamps `presented_at` the moment `campaign_ask` returns and `answered_at` when the
next user message arrives, per conversation (agent_runtime/infrastructure/checkpoint_marker.py).
This only reads it — the agent cannot write its own approval.
"""

from __future__ import annotations

import json

from agent_runtime.application.run_context import current_run_context

from ad_generation.domain.gate_approval import GateApproval
from ad_generation.infrastructure.run_workspace import RunWorkspace

FILE = ".studio/checkpoint.json"


class DaemonCheckpointLedger:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def latest(self) -> GateApproval:
        ctx = current_run_context()
        session = str(getattr(ctx, "session_key", "") or "") if ctx else ""
        if not session:
            raise RuntimeError("this run has no conversation to read approvals from")
        path = self._ws.path(FILE)
        if not path.is_file():
            return GateApproval(0.0, 0.0)
        record = (json.loads(path.read_text(encoding="utf-8")).get("sessions") or {}).get(session) or {}
        return GateApproval(
            presented_at=float(record.get("presented_at") or 0.0),
            answered_at=float(record.get("answered_at") or 0.0),
        )
