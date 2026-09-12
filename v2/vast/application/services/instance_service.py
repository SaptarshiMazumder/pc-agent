"""One GPU per account: getting it, keeping it alive, and giving it back.

ENSURE DOES NOT BLOCK. Renting and booting takes minutes; a request that waited would hold a
worker and time out anyway. So `ensure` claims the slot, starts the rental and returns
`starting` — and each later call refreshes the address until one exists. Readiness needs no
background poller, because the caller is already polling.

LAZY BY DESIGN. Nothing here runs when a chat opens. The agent calls `ensure` at the first step
that genuinely needs a GPU, because research and workflow design need documentation, not
hardware. Renting on session open would bill for every chat someone opens and abandons.

THE SLOT BELONGS TO THE ACCOUNT, NOT THE CHAT. Ten chats, one machine — settled by `claim`,
which is settled by a unique index, so a second chat cannot rent a second GPU however exactly it
races the first.
"""

from __future__ import annotations

import calendar
import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from vast.application.instance_settings import InstanceSettings
from vast.application.interfaces.gpu_marketplace import GpuMarketplace
from vast.application.interfaces.instance_store import InstanceStore
from vast.domain.errors import (
    BudgetExhausted,
    CapacityFull,
    MarketplaceError,
    NoOfferAvailable,
    SlotLost,
)
from vast.domain.instance import InstanceRow, label_for

log = logging.getLogger("vast.instances")


class InstanceService:
    """The whole lifecycle, with no HTTP and no SQL in sight."""

    def __init__(
        self,
        *,
        db: Callable[[], AbstractContextManager[Any]],
        store: InstanceStore,
        marketplace: Callable[[], GpuMarketplace],
        settings: InstanceSettings,
        now: Callable[[], float],
    ) -> None:
        # `marketplace` is a FACTORY, not an instance. Composition happens at import time while
        # the API key arrives later from the secret store; a client built eagerly would capture
        # an empty key and report "not configured" forever on a perfectly good deployment.
        self._db = db
        self._store = store
        self._marketplace = marketplace
        self._settings = settings
        self._now = now

    @property
    def configured(self) -> bool:
        return self._marketplace().configured

    def clock(self) -> float:
        """The service's own notion of now, so callers that must compare against a stored
        timestamp use the same clock this module writes with rather than their own."""
        return self._now()

    # ------------------------------------------------------------------ ensure

    def ensure(self, account_id: str) -> InstanceRow:
        """This account's instance, renting one if it has none. Safe to call repeatedly."""
        now = self._now()
        with self._db() as c:
            row, mine = self._store.claim(c, account_id, now)

        if not mine:
            # Another call owns the rental. Refresh in case it became reachable since — but
            # never start a second one.
            return self._refresh(row)

        try:
            return self._rent(row, now)
        except Exception as e:  # noqa: BLE001 — re-raised below, after the slot is freed
            # THE SLOT MUST BE RELEASED, or one bad offer becomes a permanent outage for this
            # account: a row left in 'starting' blocks every future claim it will ever make.
            with self._db() as c:
                self._store.mark_dead(c, row.id, reason=f"rental failed: {e}", now=self._now())
            log.exception("vast rental failed for account %s", account_id)
            raise

    def _rent(self, row: InstanceRow, now: float) -> InstanceRow:
        # THE CAPS ARE CHECKED HERE AND NOWHERE ELSE, because this is the only method that
        # commits money. `ensure` on an account that already has a machine never reaches this,
        # which is deliberate: a user mid-job is not cut off the moment they cross a line, they
        # are refused the NEXT rental. Cutting a running render dead would waste the very money
        # the cap exists to protect.
        self._check_caps(row.account_id, now)

        market = self._marketplace()
        cfg = self._settings
        offers = market.search_offers(
            max_hourly_usd=cfg.max_hourly_usd,
            min_vram_gb=cfg.min_vram_gb,
            min_reliability=cfg.min_reliability,
            min_cuda=cfg.min_cuda,
            min_inet_down=cfg.min_inet_down,
            gpu_allowlist=cfg.gpu_allowlist,
        )
        if not offers:
            raise NoOfferAvailable(
                f"no machine with {cfg.min_vram_gb}GB+ VRAM at or below "
                f"${cfg.max_hourly_usd:.2f}/hr right now"
            )
        offer = offers[0]
        if offer.hourly_usd > cfg.max_hourly_usd:
            # The search already filtered on price. This catches a marketplace that ignored it,
            # which is the difference between a limit and a suggestion.
            raise NoOfferAvailable(
                f"offer {offer.offer_id} is ${offer.hourly_usd:.2f}/hr, over the "
                f"${cfg.max_hourly_usd:.2f} ceiling"
            )

        instance_id = market.create(
            offer.offer_id,
            label=label_for(row.id),
            image=cfg.image,
            disk_gb=cfg.disk_gb,
            comfy_port=cfg.comfy_port,
            publish_ports=cfg.publish_ports,
        )
        with self._db() as c:
            self._store.mark_running(
                c,
                row.id,
                instance_id=instance_id,
                machine_id=offer.machine_id,
                hourly_usd=offer.hourly_usd,
                now=now,
            )
            fresh = self._store.by_id(c, row.id)
        if fresh is None:
            raise SlotLost(f"row {row.id} disappeared while being rented")
        return fresh

    def _check_caps(self, account_id: str, now: float) -> None:
        """Refuse a NEW rental that would breach either limit."""
        cfg = self._settings
        with self._db() as c:
            live = self._store.count_live(c) if cfg.max_live_instances else 0
            spent = (
                self._store.spend_since(c, account_id, month_start(now), now)
                if cfg.monthly_cap_usd
                else 0.0
            )

        # `count_live` already includes the row this call just claimed, so the limit is breached
        # only when the count EXCEEDS it — at exactly the limit, this rental is the last allowed.
        if cfg.max_live_instances and live > cfg.max_live_instances:
            raise CapacityFull(
                f"{cfg.max_live_instances} machines are already running, which is the limit. "
                "One will free up shortly — try again in a few minutes."
            )
        if cfg.monthly_cap_usd and spent >= cfg.monthly_cap_usd:
            raise BudgetExhausted(
                f"this account has used ${spent:.2f} of its ${cfg.monthly_cap_usd:.2f} GPU "
                "allowance this month, so no new machine can be started until the month turns."
            )

    def spend_this_month(self, account_id: str) -> dict:
        """What this account has run up, and what it is allowed. For the panel and for support."""
        now = self._now()
        with self._db() as c:
            spent = self._store.spend_since(c, account_id, month_start(now), now)
        cap = self._settings.monthly_cap_usd
        return {
            "spent_usd": round(spent, 4),
            "cap_usd": cap,
            "remaining_usd": round(max(0.0, cap - spent), 4) if cap else None,
        }

    def _refresh(self, row: InstanceRow) -> InstanceRow:
        """Ask whether a starting instance has an address yet, and record it if so.

        A marketplace failure is logged and swallowed HERE, and only here: the caller is
        polling, the next poll tries again, and a transient blip must not present as a hard
        error on a machine that is booting perfectly well.
        """
        if row.state != "starting" or row.instance_id is None:
            return row
        try:
            live = self._marketplace().get_instance(row.instance_id)
        except MarketplaceError:
            log.warning("could not refresh instance %s", row.instance_id, exc_info=True)
            return row
        if live is not None and live.dead:
            # THIS MACHINE ANNOUNCED ITS OWN DEATH. Destroy it and free the slot NOW: the caller
            # is polling, so their very next `ensure` rents a different host and the user sees a
            # slightly longer start rather than a fifteen-minute wait for the reaper's grace to
            # expire on a box that was never going to serve. Two of four real rentals needed
            # this, one of them after the reliability filter was already in place.
            log.warning("vast: host %s failed to start (%s)", live.instance_id, live.status_msg)
            self.release(row.account_id, reason=f"host failed to start: {live.status_msg}"[:200])
            with self._db() as c:
                return self._store.by_id(c, row.id) or row
        if live is None or not live.url:
            return row
        with self._db() as c:
            self._store.mark_ready(c, row.id, url=live.url, now=self._now())
            return self._store.by_id(c, row.id) or row

    # ------------------------------------------------------------------ liveness

    def heartbeat(self, account_id: str, lease_seconds: float = 0.0) -> tuple[bool, InstanceRow | None]:
        """"Still using it." Every agent tool call makes one; a submitted render also takes a
        lease, so a long job is not mistaken for an abandoned one."""
        now = self._now()
        capped = min(max(lease_seconds, 0.0), self._settings.max_lease_seconds)
        lease_until = now + capped if capped else 0.0
        with self._db() as c:
            alive = self._store.heartbeat(c, account_id, now=now, lease_until=lease_until)
            return alive, self._store.live_for(c, account_id)

    def status(self, account_id: str) -> InstanceRow | None:
        with self._db() as c:
            row = self._store.live_for(c, account_id)
        return self._refresh(row) if row else None

    # ------------------------------------------------------------------ release

    def release(self, account_id: str, *, reason: str = "released") -> bool:
        """Give the machine back now rather than waiting for the idle sweep.

        DESTROY FIRST, THEN MARK DEAD. The other order frees the account's slot while the
        instance is still billing, so the next request rents a second GPU alongside one we have
        merely stopped tracking.
        """
        with self._db() as c:
            row = self._store.live_for(c, account_id)
        if row is None:
            return False
        if row.instance_id is not None:
            self._marketplace().destroy(row.instance_id)
        with self._db() as c:
            self._store.mark_dead(c, row.id, reason=reason, now=self._now())
        return True


def month_start(now: float) -> float:
    """Midnight UTC on the 1st of the month containing `now`.

    UTC ON PURPOSE. A cap that reset on the server's local midnight would move with the
    deployment's timezone, and "the month" has to mean the same thing to the check and to the
    invoice it is protecting.

    `calendar.timegm`, NOT `time.mktime`. mktime interprets its argument as LOCAL time, so the
    "- time.timezone" correction this first used was both a fudge and wrong across DST — and on
    Windows it raises OverflowError outright for any date near the epoch, which is how the tests
    found it. timegm is gmtime's exact inverse and has neither problem.
    """
    t = time.gmtime(now)
    return float(calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, 0)))
