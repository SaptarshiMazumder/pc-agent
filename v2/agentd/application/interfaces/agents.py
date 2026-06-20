"""AgentRegistry — the contract for resolving which agent handles a session.

The application layer depends on THIS port, not on how agents are stored. A
file-backed registry (reads `agents/<id>/` dirs) satisfies it today; a cloud/db
registry would satisfy the same interface and slot in by config — without the
use-case changing.
"""

from __future__ import annotations

from typing import Protocol

from agentd.domain.agent import AgentSpec


class AgentRegistry(Protocol):
    def resolve(self, session_key: str) -> AgentSpec:
        """The agent for a session key (defaults to ``main`` for legacy/unknown keys)."""
        ...

    def get(self, agent_id: str) -> AgentSpec:
        """The agent by id (raises ``KeyError`` if unknown)."""
        ...

    def list_ids(self) -> list[str]:
        """All known agent ids."""
        ...
