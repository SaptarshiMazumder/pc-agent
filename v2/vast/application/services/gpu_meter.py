"""GpuMeter — the rented minutes become a charge on the person's account, every sweep.

THE MARKETPLACE BILLS THE PLATFORM BY THE SECOND; THIS BILLS THE PERSON THE SAME WAY. Each sweep,
every row that has time nobody has paid for yet — a live machine since the last sweep, a dead
one up to the moment it died — is charged for exactly that slice, at its own hourly rate times
the configured markup, through the AccountCharges port. The row remembers how far it has been
billed (`billed_until`), so a sweep that runs twice charges nothing twice, and a sweep that did
not run for an hour charges the hour.

MARK FIRST, CHARGE SECOND. A crash between the two under-charges one slice; the other order
double-charges it. The one that errs in the user's favour is the one that is never noticed.

A SHORT CHARGE IS A STOP. The accounts service never overdrafts: a slice the balance could not
cover comes back `covered=False`, and the meter hands the row to the reaper so the machine is
destroyed with a reason the person can read — "out of credits" — instead of running on for free.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from vast.application.instance_settings import InstanceSettings
from vast.application.interfaces.account_charges import AccountCharges
from vast.application.interfaces.instance_store import InstanceStore
from vast.domain.instance import InstanceRow

log = logging.getLogger("vast.meter")

#: A LIVE machine's slice shorter than this waits for the next sweep: a fraction of a credit
#: rounds up to one, and sixty of those an hour is money for nothing. A dead machine's tail is
#: settled whatever its length — there is no next sweep for it.
MIN_SLICE_S = 30.0

#: How long after a machine died its unbilled tail is still looked for. Bounds the scan; a tail
#: older than this belongs to a sweep that did not run for a week, and is written off rather
#: than surprising someone with last month's minutes.
DEAD_TAIL_WINDOW_S = 7 * 24 * 3600.0


class GpuMeter:
    def __init__(
        self,
        *,
        db: Callable[[], AbstractContextManager[Any]],
        store: InstanceStore,
        charges: AccountCharges | None,
        settings: InstanceSettings,
        now: Callable[[], float],
    ) -> None:
        self._db = db
        self._store = store
        self._charges = charges
        self._settings = settings
        self._now = now

    def charge_due(self, result: dict) -> list[InstanceRow]:
        """Charge every row's unpaid time up to now.

        Returns the LIVE rows whose account could not cover their slice — the ones to stop.
        `result` is the reaper's summary: this adds `charged`, `charged_usd` and `unfunded`,
        and appends to its `errors` like the sweeps do, so one dashboard reads all of it."""
        result.setdefault("charged", 0)
        result.setdefault("charged_usd", 0.0)
        result.setdefault("unfunded", 0)
        result.setdefault("errors", [])
        if self._charges is None:
            return []  # nobody to charge: the platform pays, as before this existed
        now = self._now()
        with self._db() as c:
            rows = self._store.unbilled_rows(c, until=now, since=now - DEAD_TAIL_WINDOW_S)
        short: list[InstanceRow] = []
        for row in rows:
            end = min(now, row.dead_at) if row.dead_at is not None else now
            start = max(row.billed_until, row.created_at)
            seconds = end - start
            if seconds <= 0 or (seconds < MIN_SLICE_S and row.dead_at is None):
                continue
            usd = row.hourly_usd * self._settings.credit_markup * seconds / 3600.0
            with self._db() as c:
                self._store.mark_billed(c, row.id, until=end)
            try:
                outcome = self._charges.charge(
                    row.account_id,
                    usd,
                    event_id=f"gpu:{row.id}:{int(end)}",
                    label="gpu",
                    agent_id=row.agent_id,
                )
            except Exception as e:  # noqa: BLE001 — one account's failure must not stop the rest
                result["errors"].append(f"charge {row.id} failed: {e}")
                log.warning("vast meter: charge for %s failed", row.id, exc_info=True)
                continue
            result["charged"] += 1
            result["charged_usd"] = round(result["charged_usd"] + usd, 6)
            if not outcome.covered:
                result["unfunded"] += 1
                log.info(
                    "vast meter: %s (account %s) could not cover $%.4f of GPU time",
                    row.id, row.account_id, usd,
                )
                if row.dead_at is None:
                    short.append(row)
        return short
