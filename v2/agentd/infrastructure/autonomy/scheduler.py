"""HeartbeatScheduler — the ONE shared loop that wakes agents on their interval.

NOT one runner per agent: a single asyncio loop holds every agent's next-due time
(derived from its `agent.toml` heartbeat interval, or a global default) and, on each
tick, fires whichever agents are due — by calling the injected `fire(agent_id)`
(the gateway turns that into a heartbeat run through the normal path).

Default OFF: if disabled, `run()` returns immediately and nothing fires. Timing is
in-memory in 2a (a missed window after a restart just delays the next tick by up to
one interval); durable scheduling (cron jobs that must survive restarts) is 2b.

`due()` is pure/synchronous so the timing logic is unit-tested without real clocks.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime

log = logging.getLogger("agentd")

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_interval(text) -> float | None:
    """'30s' / '15m' / '2h' / '1d' -> seconds. Returns None for empty/invalid."""
    if not text:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", str(text).lower())
    if not m:
        return None
    return int(m.group(1)) * _UNITS[m.group(2)]


def parse_active_hours(text):
    """'08:00-22:00' -> (start_minute, end_minute); None = always active."""
    if not text:
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", str(text))
    if not m:
        return None
    a = int(m.group(1)) * 60 + int(m.group(2))
    b = int(m.group(3)) * 60 + int(m.group(4))
    return (a, b)


def in_active_hours(window, now_minute: int) -> bool:
    """True if now is inside the window (handles overnight ranges like 22:00-06:00)."""
    if window is None:
        return True
    a, b = window
    return a <= now_minute < b if a <= b else (now_minute >= a or now_minute < b)


class HeartbeatScheduler:
    def __init__(self, registry, fire, *, enabled: bool = False,
                 default_interval: str = "", active_hours: str = "",
                 poll_seconds: float = 15.0, task_store=None, fire_task=None):
        self._registry = registry              # list_ids() + get(id).heartbeat
        self._fire = fire                      # async (agent_id) -> None  (heartbeat)
        self._enabled = enabled
        self._default = default_interval
        self._window = parse_active_hours(active_hours)
        self._poll = poll_seconds
        self._task_store = task_store          # durable cron ledger (Phase 2b), or None
        self._fire_task = fire_task            # async (ScheduledTask) -> None, or None
        self._intervals: dict[str, float] = {}   # agent_id -> seconds
        self._next_due: dict[str, float] = {}    # agent_id -> epoch seconds

    # ---- timing (pure, unit-testable) --------------------------------------

    def load(self, now: float) -> None:
        """Build per-agent intervals from the registry; first tick is one interval out."""
        for agent_id in self._registry.list_ids():
            iv = parse_interval(getattr(self._registry.get(agent_id), "heartbeat", None)
                                or self._default)
            if iv:
                self._intervals[agent_id] = iv
                self._next_due.setdefault(agent_id, now + iv)

    def due(self, now: float) -> list[str]:
        """Agents whose next-due time has passed; advances each one. (No IO.)"""
        out = []
        for agent_id, iv in self._intervals.items():
            if now >= self._next_due.get(agent_id, now + iv):
                out.append(agent_id)
                self._next_due[agent_id] = now + iv
        return out

    def active_now(self, now_dt: datetime) -> bool:
        return in_active_hours(self._window, now_dt.hour * 60 + now_dt.minute)

    # ---- the loop -----------------------------------------------------------

    async def run(self) -> None:
        if not self._enabled:
            return
        self.load(time.time())
        if self._intervals:
            log.info("heartbeat scheduler: ON for %s", ", ".join(sorted(self._intervals)))
        else:
            log.info("heartbeat scheduler: ON but no agent declares an interval")
        while True:
            try:
                # heartbeats respect active-hours (don't pester at night)
                if self.active_now(datetime.now().astimezone()):
                    for agent_id in self.due(time.time()):
                        try:
                            await self._fire(agent_id)
                        except Exception as e:  # noqa: BLE001 — one tick must not kill the loop
                            log.warning("heartbeat fire(%s) failed: %s", agent_id, e)
                # cron tasks fire at their explicit time, regardless of active-hours.
                # fire_task returns False when the agent's lane is busy -> leave the task
                # due so it retries next poll (don't drop a one-shot).
                if self._task_store is not None and self._fire_task is not None:
                    for task in self._task_store.due(time.time()):
                        try:
                            fired = await self._fire_task(task)
                        except Exception as e:  # noqa: BLE001
                            log.warning("cron fire(%s) failed: %s", task.id, e)
                            fired = True   # a created-but-failed run still advances (no storm)
                        if fired:
                            self._task_store.advance(task.id, time.time())  # reschedule/disable
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick error")
            await asyncio.sleep(self._poll)
