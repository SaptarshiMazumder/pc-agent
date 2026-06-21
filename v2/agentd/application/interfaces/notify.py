"""Notify ports — outbound user notifications (Phase 5a).

Two ports (interface segregation):
  * ``Notifier``   SENDS a notification through whatever channel(s) are wired —
                   client push, a durable store, email/WhatsApp later. Adding a
                   channel = a new adapter; the gateway depends only on this port.
  * ``NotifyStore`` PERSISTS notifications so they survive while no client is
                    connected, and can be listed / acknowledged later.
"""

from __future__ import annotations

from typing import Protocol

from agentd.domain.notify import Notification


class Notifier(Protocol):
    async def notify(self, n: Notification) -> None:
        """Deliver a notification through the wired channel(s). Must not raise."""
        ...


class NotifyStore(Protocol):
    # Distinct method names (not save/list/ack) so ONE concrete store can implement
    # this alongside TaskStore/GoalStore without collisions — same pattern as GoalStore.
    def save(self, n: Notification) -> str:
        """Persist a notification; returns its id."""
        ...

    def notifications(self, agent_id: str | None = None, unread_only: bool = False,
                      limit: int = 50) -> list[Notification]:
        """Notifications (optionally one agent / only unread), newest first."""
        ...

    def ack(self, notif_id: str) -> bool:
        """Mark one read; True if it existed."""
        ...
