"""One GPU per account: getting it, keeping it alive, and giving it back.

ENSURE DOES NOT BLOCK. Renting and booting takes minutes; a request that waited would hold a
worker and time out anyway. So `ensure` claims the slot, starts the rental and returns
`starting` — and each later call refreshes the address until one exists AND ANSWERS. A machine
is `running` at the marketplace for minutes before ComfyUI inside it serves, and `ready` here
means it serves: the platform asks it before saying so. Readiness needs no background poller,
because the caller is already polling.

CALLED EAGERLY, BY DESIGN — and this was "lazy by design" first. The studio window calls `ensure`
the moment it opens and the agent calls it as its first step, because a cold machine takes minutes
to become reachable and that wait belongs while the user reads a plan, not after it. The earlier
worry — renting on session open bills for every chat someone opens and abandons — is what the
idle reaper answers: a machine nobody uses stops itself. This service does not decide WHEN it is
called; it only has to make every call safe to repeat.

THE SLOT BELONGS TO THE ACCOUNT, NOT THE CHAT. Ten chats, one machine — settled by `claim`,
which is settled by a unique index, so a second chat cannot rent a second GPU however exactly it
races the first.
"""

from __future__ import annotations

import calendar
import logging
import secrets
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from vast.application.instance_settings import InstanceSettings
from vast.application.interfaces.gpu_marketplace import GpuMarketplace
from vast.application.interfaces.instance_probe import InstanceProbe
from vast.application.interfaces.instance_store import InstanceStore
from vast.application.offer_ranking import rank_offers
from vast.domain.errors import (
    BudgetExhausted,
    CapacityFull,
    MarketplaceError,
    NoOfferAvailable,
    OfferGone,
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
        probe: InstanceProbe,
    ) -> None:
        # `marketplace` is a FACTORY, not an instance. Composition happens at import time while
        # the API key arrives later from the secret store; a client built eagerly would capture
        # an empty key and report "not configured" forever on a perfectly good deployment.
        self._db = db
        self._store = store
        self._marketplace = marketplace
        self._settings = settings
        self._now = now
        self._probe = probe

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
            # A POLL IS INTEREST. Nothing else touches last_seen_at while a machine boots —
            # claim returns the row as it is, and the agent takes no lease until it renders —
            # so a machine polled every ten seconds looked idle from the moment it was rented
            # and the reaper destroyed it mid-boot at 600s, in front of the user who was
            # waiting for it. Every ensure marks the row seen; a machine nobody asks about for
            # ten minutes is still reaped, which is the rule that was meant.
            with self._db() as c:
                self._store.heartbeat(c, account_id, now=now)
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
        # Hosts that failed to start recently, from our own ledger. Cheapest-first would rent
        # the same dead machine straight back — it is still the cheapest, and its failure did
        # not move its price or its provider score.
        with self._db() as c:
            excluded = (
                self._store.failed_machines_since(c, now - cfg.failed_machine_cooldown_seconds)
                if cfg.failed_machine_cooldown_seconds
                else set()
            )
        offers = market.search_offers(
            max_hourly_usd=cfg.max_hourly_usd,
            min_vram_gb=cfg.min_vram_gb,
            min_reliability=cfg.min_reliability,
            min_cuda=cfg.min_cuda,
            min_inet_down=cfg.min_inet_down,
            gpu_allowlist=cfg.gpu_allowlist,
            secure_cloud_only=cfg.secure_cloud_only,
            exclude_machines=excluded,
            min_compute_cap=cfg.min_compute_cap,
            gpu_denylist=cfg.gpu_denylist,
            require_verified=cfg.require_verified,
            min_disk_gb=cfg.disk_gb,
            limit=cfg.search_limit,
        )
        # QUALITY FIRST, PRICE WITHIN — see offer_ranking. The search returns the pool under
        # the ceiling cheapest-first; this reorders it so the offer-walk below starts from the
        # best card the ceiling allows rather than the cheapest thing that cleared the filters.
        offers = rank_offers(offers, cfg.gpu_catalogue)
        if not offers:
            tier = "Secure Cloud (datacenter) machine" if cfg.secure_cloud_only else "machine"
            vram = f" with {cfg.min_vram_gb}GB+ VRAM" if cfg.min_vram_gb else ""
            raise NoOfferAvailable(
                f"no {tier}{vram} at or below ${cfg.max_hourly_usd:.2f}/hr right now"
            )
        # THE FIRST CANDIDATE IS THE ONE MOST LIKELY TO BE GONE. The list is cheapest-first, and
        # the cheapest listing is exactly what every other buyer is about to take, so on a busy
        # day the rent races the search and loses by a second. A single attempt turned that race
        # into "gpu_ensure failed": the slot was freed, but nothing called again, and the user
        # sat with no machine. Walk the candidates instead — each is a different host — and
        # only when every one has gone is there genuinely nothing to rent right now.
        # THIS RENTAL'S OWN SECRET, minted before the loop so every attempt in it shares one:
        # the machine that finally starts is the one it was set on.
        auth_token = secrets.token_urlsafe(24)
        gone: list[int] = []
        instance_id: int | None = None
        for offer in offers:
            if offer.hourly_usd > cfg.max_hourly_usd:
                # The search already filtered on price. This catches a marketplace that ignored
                # it, which is the difference between a limit and a suggestion.
                raise NoOfferAvailable(
                    f"offer {offer.offer_id} is ${offer.hourly_usd:.2f}/hr, over the "
                    f"${cfg.max_hourly_usd:.2f} ceiling"
                )
            try:
                instance_id = market.create(
                    offer.offer_id,
                    label=label_for(row.id),
                    image=cfg.image,
                    disk_gb=cfg.disk_gb,
                    comfy_port=cfg.comfy_port,
                    publish_ports=cfg.publish_ports,
                    env=cfg.container_env,
                    auth_token=auth_token,
                )
            except OfferGone as e:
                # Not a fault, and not ours to surface yet: the next candidate is a different
                # machine. Anything ELSE `create` raises — a refused request, a credit problem,
                # a reply with no instance id — propagates untouched, because it would refuse
                # the next offer just the same.
                log.info("vast: %s — trying the next candidate", e)
                gone.append(offer.offer_id)
                continue
            break
        if instance_id is None:
            raise NoOfferAvailable(
                f"all {len(gone)} candidate machines were taken by other buyers before one could "
                "be rented — try again in a moment"
            )
        with self._db() as c:
            self._store.mark_running(
                c,
                row.id,
                instance_id=instance_id,
                machine_id=offer.machine_id,
                hourly_usd=offer.hourly_usd,
                now=now,
                auth_token=auth_token,
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
        if not self._probe.answers(live.url, row.auth_token):
            # AN ADDRESS THAT DOES NOT ANSWER IS NOT READY. The marketplace says `running` the
            # moment the container is up; ComfyUI inside it is still pulling the image and
            # loading models for minutes after. Handing the address out in that window is
            # what made an agent conclude the box was stuck and release it mid-boot.
            now = self._now()
            if now - row.created_at > self._settings.starting_grace_seconds:
                # It has had the whole start-up grace and never served. Give it back — the
                # reason matches the cooldown's pattern, so the next rental skips this host —
                # and the caller's next `ensure` rents a different one.
                log.warning(
                    "vast: host %s never answered within %ss; releasing",
                    live.instance_id, int(self._settings.starting_grace_seconds),
                )
                self.release(
                    row.account_id,
                    reason="host failed to start: ComfyUI never answered within the start-up grace",
                )
                with self._db() as c:
                    return self._store.by_id(c, row.id) or row
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
