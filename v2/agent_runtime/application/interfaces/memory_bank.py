"""MemoryBank port — the agent's long-term, searchable memory (Phase 3 / S4).

Separate from SessionStore (transcripts): this stores durable facts/notes and recalls them
by keyword across sessions. The application depends on this port; SQLite+FTS is one adapter,
a vector DB another — swap by config without touching callers.
"""

from __future__ import annotations

from typing import Protocol

from agent_runtime.domain.memory import MemoryItem


class MemoryBank(Protocol):
    def save(self, item: MemoryItem) -> str:
        """Persist a memory item; returns its id."""
        ...

    def search(self, agent_id: str | None, query: str, limit: int = 5) -> list[MemoryItem]:
        """Keyword-recall an agent's memories (all agents if agent_id is None)."""
        ...

    def get(self, item_id: str) -> MemoryItem | None:
        """Fetch one item by id."""
        ...

    def recent(self, agent_id: str | None = None, limit: int = 20) -> list[MemoryItem]:
        """Most-recent items, newest first."""
        ...

    def delete(self, item_id: str) -> bool:
        """Remove one item; True if it existed."""
        ...

    def purge_agent(self, agent_id: str) -> int:
        """Remove all of one agent's memory; returns the row count removed."""
        ...

    def close(self) -> None: ...
