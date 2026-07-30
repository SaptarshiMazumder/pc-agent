"""Notification — a message the agent/gateway pushes TO the user (Phase 5a).

Outbound only: the agent *reaches the user* (e.g. a scheduled run got blocked and
needs authorization). This is gateway-driven and reliable — triggered by the run
outcome — as opposed to the agent's own tool-driven messaging (which only happens if
the agent is running and chooses to). Pure domain: no IO.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Notification:
    id: str
    agent_id: str
    kind: str  # blocked | failed | info
    text: str  # headline, e.g. "job-yucho blocked"
    detail: str = ""  # reason / context, e.g. "needs Google Drive authorization"
    created_at: float = 0.0
    read: bool = False
