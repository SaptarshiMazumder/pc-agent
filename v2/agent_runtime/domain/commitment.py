"""Commitment — a tracked open loop / follow-up obligation (S15).

A promise the agent made or a follow-up it owes ("circle back to Jane on Friday"). Unlike a
cron job (which EXECUTES at a time) a commitment is a tracked note with a status the agent
reviews and resolves. Pure domain.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Commitment:
    id: str
    agent_id: str
    text: str
    due_at: float | None = None
    status: str = "open"  # open | done | dropped
    created_at: float = 0.0
