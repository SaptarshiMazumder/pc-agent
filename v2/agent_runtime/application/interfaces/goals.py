"""GoalStore — the contract for persisting a session's goal (Phase 2b).

Kept SEPARATE from TaskStore (interface segregation): the goal tool depends only on
this narrow port. One concrete store (SqliteTaskStore) may implement both — the
consumers don't care.
"""

from __future__ import annotations

from typing import Protocol

from agent_runtime.domain.autonomy import Goal


class GoalStore(Protocol):
    def create_goal(self, goal: Goal) -> str:
        """Persist a goal; returns its id."""
        ...

    def active_goal(self, session_key: str) -> Goal | None:
        """The most recent still-active goal for a session, or None."""
        ...

    def update_goal(self, goal_id: str, status: str) -> bool:
        """Set a goal's status (complete | blocked); True if it existed."""
        ...
