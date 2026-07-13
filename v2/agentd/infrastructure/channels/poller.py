"""ChannelPoller — ONE shared loop that polls every channel for inbound messages and
hands each new one to ``fire`` (the gateway's channel-run path). Mirrors the heartbeat
scheduler: one driver for all channels, defensive (a bad channel never kills the loop).
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


class ChannelPoller:
    def __init__(self, channels: list, fire, interval: float = 15.0):
        self._channels = list(channels)
        self._fire = fire  # async (channel, InboundMessage) -> None
        self._interval = interval

    async def tick(self) -> None:
        """One poll round across all channels (extracted for testability)."""
        for ch in self._channels:
            try:
                messages = await ch.poll()
            except Exception:  # noqa: BLE001 — one bad channel never stops the rest
                log.warning("channel poll failed (%s)", getattr(ch, "name", "?"), exc_info=True)
                continue
            for msg in messages:
                try:
                    await self._fire(ch, msg)
                except Exception:  # noqa: BLE001
                    log.warning("channel fire failed (%s)", getattr(ch, "name", "?"), exc_info=True)

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self._interval)
