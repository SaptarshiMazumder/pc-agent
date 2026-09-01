"""MemoryItem — one durable unit of an agent's long-term memory (Phase 3 / S4).

Distinct from a session transcript (SessionStore): these are facts/notes the agent recalls
ON DEMAND across conversations, so it learns and stops re-asking. Pure domain: no IO.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryItem:
    id: str
    agent_id: str
    source: str  # note | memory | session | consolidated
    text: str
    created_at: float = 0.0
