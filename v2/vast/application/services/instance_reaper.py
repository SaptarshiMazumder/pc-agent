"""The thing that stops paying. Two sweeps, run on a clock, and the second is the important one.

IDLE SWEEP reads our table: rows nobody has touched for `idle_seconds`, with no live lease, get
destroyed. This is the expected path and it handles the ordinary case — a user wandered off.

ORPHAN SWEEP reads the MARKETPLACE and compares. It exists because of an asymmetry that is easy
to miss:

    our table is the source of truth for what SHOULD exist
    the marketplace is the source of truth for what DOES exist

The idle sweep can only ever see instances it already knows about. A rental that succeeded and
whose database write then failed is invisible to it — forever. That single case is the one that
bills for a month, and only a sweep that starts from the marketplace's own list can catch it.
The label stamped at rental time is what makes the comparison possible.

FOREIGN MACHINES ARE LEFT ALONE. An instance whose label is not ours was started by a human on
the same marketplace account, and killing someone's work because it was in our list would be far
worse than paying for it. They are counted and reported, never destroyed.

THE SWEEP RECORDS THAT IT RAN. A reaper that silently stops looks exactly like a reaper with
nothing to do, so the timestamp is the input to a dead-man alarm — see `last_sweep`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from vast.application.instance_settings import InstanceSettings
from vast.application.interfaces.gpu_marketplace import GpuMarketplace
from vast.application.interfaces.instance_store import InstanceStore
from vast.domain.errors import MarketplaceError
from vast.domain.instance import row_id_from_label

log = logging.getLogger("vast.reaper")


class InstanceReaper:
    def __init__(
        self,
        *,
        db: Callable[[], AbstractContextManager[Any]],
        store: InstanceStore,
        marketplace: Callable[[], GpuMarketplace],
        settings: InstanceSettings,
        now: Callable[[], float],
    ) -> None:
        self._db = db
        self._store = store
        self._marketplace = marketplace
        self._settings = settings
        self._now = now

    def sweep(self) -> dict:
        """Both sweeps, in order. Returns a summary a dashboard and an alarm can both read.

        ERRORS ARE COLLECTED, NOT RAISED. One instance that refuses to die must not stop the
        others being reaped — that would turn a single stuck machine into a bill for all of
        them. Every failure is counted and named in the result, so a run that half-worked is
        visible rather than silently partial.
        """
        now = self._now()
        result: dict = {"idle": 0, "orphans": 0, "vanished": 0, "foreign": 0, "errors": []}

        self._sweep_idle(now, result)
        self._sweep_orphans(now, result)

        with self._db() as c:
            self._store.record_sweep(c, now=now)
        log.info("vast reap: %s", result)
        return result

    # ------------------------------------------------------------------ idle

    def _sweep_idle(self, now: float, result: dict) -> None:
        cfg = self._settings
        with self._db() as c:
            rows = self._store.live_rows(c)

        for row in rows:
            # A row still waiting for its rental to come back is judged on AGE, not on contact:
            # nothing will ever touch it, so the idle clock would never start.
            if row.state == "starting" and row.instance_id is None:
                if now - row.created_at < cfg.starting_grace_seconds:
                    continue
                reason = "rental never returned an instance id"
            elif row.idle_since(now) < cfg.idle_seconds:
                continue
            else:
                reason = f"idle for {int(row.idle_since(now))}s"

            if self._destroy(row.instance_id, row.id, reason, result):
                result["idle"] += 1

    # ------------------------------------------------------------------ orphans

    def _sweep_orphans(self, now: float, result: dict) -> None:
        try:
            live = self._marketplace().list_instances()
        except MarketplaceError as e:
            # Cannot see the marketplace this minute. Say so and stop — the next run tries
            # again, and guessing at what to destroy without the list would be far worse.
            result["errors"].append(f"list_instances failed: {e}")
            log.warning("vast reap: could not list instances", exc_info=True)
            return

        seen_instance_ids: set[int] = set()
        for machine in live:
            seen_instance_ids.add(machine.instance_id)
            row_id = row_id_from_label(machine.label)
            if not row_id:
                # Someone else's box on this account. Never ours to kill.
                result["foreign"] += 1
                continue
            with self._db() as c:
                row = self._store.by_id(c, row_id)
            if row is not None and row.live:
                continue  # tracked and wanted
            reason = "orphaned: no live row owns it"
            if self._destroy(machine.instance_id, row_id if row else None, reason, result):
                result["orphans"] += 1

        # THE OTHER DIRECTION: a row we believe is live whose machine is gone (destroyed by
        # hand, or by the marketplace). Left alone it would hold the account's only slot
        # forever, so the user could never get another GPU.
        with self._db() as c:
            rows = self._store.live_rows(c)
        for row in rows:
            if row.instance_id is None or row.instance_id in seen_instance_ids:
                continue
            with self._db() as c:
                self._store.mark_dead(c, row.id, reason="instance is gone", now=now)
            result["vanished"] += 1

    # ------------------------------------------------------------------ killing

    def _destroy(self, instance_id: int | None, row_id: str | None, reason: str, result: dict
                 ) -> bool:
        """Destroy first, then mark dead — never the reverse.

        Marking dead first frees the account's slot while the machine is still billing, so the
        next request rents a second GPU alongside one we have merely stopped tracking.
        """
        try:
            if instance_id is not None:
                self._marketplace().destroy(instance_id)
        except MarketplaceError as e:
            result["errors"].append(f"destroy {instance_id} failed: {e}")
            log.warning("vast reap: could not destroy %s", instance_id, exc_info=True)
            return False
        if row_id:
            with self._db() as c:
                self._store.mark_dead(c, row_id, reason=reason, now=self._now())
        return True

    # ------------------------------------------------------------------ liveness

    def last_sweep(self) -> float:
        """When a sweep last completed. 0 = never.

        THE DEAD-MAN INPUT. Nothing else distinguishes "no instances needed reaping" from "the
        reaper has not run since Tuesday", and those two look identical on every other metric.
        """
        with self._db() as c:
            return self._store.last_sweep(c)
