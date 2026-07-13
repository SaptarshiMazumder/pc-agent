"""Notify adapters — concrete Notifier implementations (Phase 5a).

``ClientPushNotifier`` broadcasts to connected clients (live); ``StoreNotifier``
persists durably (survives offline); ``CompositeNotifier`` fans out to all. Adding an
email/WhatsApp channel later = a new adapter here wired into the composite — the
gateway never changes. Every adapter swallows its own errors: a notification must
NEVER break the run that triggered it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agentd.application.interfaces.notify import Notifier, NotifyStore
from agentd.domain.notify import Notification

log = logging.getLogger(__name__)


class ClientPushNotifier:
    """Push a notification to connected clients (live). No-op if none are connected."""

    def __init__(self, broadcast: Callable[[Notification], Awaitable[None]]):
        self._broadcast = broadcast

    async def notify(self, n: Notification) -> None:
        try:
            await self._broadcast(n)
        except Exception:  # noqa: BLE001 — notify must never break a run
            log.warning("client-push notify failed", exc_info=True)


class StoreNotifier:
    """Persist a notification so it survives while no client is connected."""

    def __init__(self, store: NotifyStore):
        self._store = store

    async def notify(self, n: Notification) -> None:
        try:
            self._store.save(n)
        except Exception:  # noqa: BLE001
            log.warning("store notify failed", exc_info=True)


class ChannelNotifier:
    """Adapt ANY Channel into a Notifier: deliver notifications to the OWNER's peer on
    that channel. This is the no-duplication seam — a transport (email/WhatsApp/Slack)
    is implemented ONCE as a Channel; its single ``send`` serves both agent replies and
    notifications. Adding a notify channel never means re-implementing the transport."""

    def __init__(self, channel, owner_peer: str):
        self._channel = channel  # any Channel (has async send(peer, text))
        self._owner = owner_peer  # where THIS user's notifications go on it

    async def notify(self, n: Notification) -> None:
        body = n.text + (f"\n\n{n.detail}" if n.detail else "")
        try:
            await self._channel.send(self._owner, body)
        except Exception:  # noqa: BLE001 — notify must never break a run
            log.warning(
                "channel notify failed (%s)", getattr(self._channel, "name", "?"), exc_info=True
            )


class CompositeNotifier:
    """Fan out to several notifiers; one failing never blocks the others."""

    def __init__(self, notifiers: list[Notifier]):
        self._notifiers = list(notifiers)

    async def notify(self, n: Notification) -> None:
        for nf in self._notifiers:
            try:
                await nf.notify(n)
            except Exception:  # noqa: BLE001
                log.warning("notifier %s failed", type(nf).__name__, exc_info=True)


def build_notifier(
    store: NotifyStore | None,
    broadcast: Callable[[Notification], Awaitable[None]],
    extra: list[Notifier] | None = None,
) -> CompositeNotifier:
    """Compose the active notify channels: live client-push + (if a store is present)
    durable persistence + any ``extra`` notifiers (e.g. ChannelNotifier for email)."""
    channels: list[Notifier] = [ClientPushNotifier(broadcast)]
    if store is not None:
        channels.append(StoreNotifier(store))
    channels.extend(extra or [])
    return CompositeNotifier(channels)
