"""TaskStore — the durable ledger of scheduled jobs (Phase 2b).

The cron tool writes tasks; the scheduler reads the due ones and fires them. The
application depends on THIS port, not on SQLite — a cloud/db store would satisfy the
same interface. Methods are synchronous (fast local writes); callers are async but
the work is sub-millisecond.
"""

from __future__ import annotations

from typing import Protocol

from agentd.domain.autonomy import ScheduledTask


class TaskStore(Protocol):
    def add(self, task: ScheduledTask) -> str:
        """Persist a task; returns its id."""
        ...

    def list(self, agent_id: str | None = None) -> list[ScheduledTask]:
        """All tasks (optionally for one agent), newest first."""
        ...

    def remove(self, task_id: str) -> bool:
        """Delete a task; True if it existed."""
        ...

    def due(self, now: float) -> list[ScheduledTask]:
        """Enabled tasks whose next_due has passed."""
        ...

    def advance(self, task_id: str, now: float) -> None:
        """After firing: reschedule a recurring task, or disable a one-shot."""
        ...

    def close(self) -> None: ...
