"""The /vast/* HTTP surface. Transport only — it maps requests onto InstanceService and shapes
the reply, and holds no lifecycle logic of its own.

WHAT A CALLER IS TOLD is deliberately less than the row holds: an address and a state. The agent
has no use for machine ids or dead-reasons, and every field published here is a field something
downstream can come to depend on.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Body, Header, HTTPException

from vast.application.services.instance_reaper import InstanceReaper
from vast.application.services.instance_service import InstanceService
from vast.domain.errors import (
    BudgetExhausted,
    CapacityFull,
    MarketplaceError,
    NoOfferAvailable,
    VastError,
)
from vast.domain.instance import InstanceRow


def _view(row: InstanceRow | None) -> dict:
    if row is None:
        return {"state": "none", "ready": False, "url": "", "auth": ""}
    return {
        "state": row.state,
        "ready": row.ready,
        "url": row.url,
        # The Authorization header value the machine expects, verbatim — the comfy tools write
        # it into the handover file and send it on every call. Only ever given to the account
        # that owns the rental, over the internal-key route this router already requires.
        "auth": f"Bearer {row.auth_token}" if row.auth_token else "",
        "hourly_usd": row.hourly_usd,
        "instance_id": row.instance_id,
    }


def build_vast_router(
    *,
    service: InstanceService,
    reaper: InstanceReaper,
    require_internal: Callable[[str | None], bool],
) -> APIRouter:
    router = APIRouter()

    def _guard(key: str | None) -> None:
        if not require_internal(key):
            raise HTTPException(status_code=401, detail="internal key required")
        if not service.configured:
            # No credentials on this deployment. Say so plainly rather than failing deeper —
            # desktop and local dev land here, and "not configured" is the honest answer.
            #
            # 501, NOT 503. 503 is this router's word for "temporary — ask again in a minute"
            # (no offer this minute, the platform at its machine limit), and the window now
            # polls on it. A deployment with no key would poll forever. 501 Not Implemented is
            # what this is: the feature does not exist on this server.
            raise HTTPException(
                status_code=501, detail="GPU renting is not configured on this deployment"
            )

    def _account(payload: dict) -> str:
        account_id = str(payload.get("account_id") or "").strip()
        if not account_id:
            raise HTTPException(status_code=400, detail="account_id required")
        return account_id

    @router.post("/vast/ensure")
    def ensure(
        payload: dict = Body(default={}), x_internal_key: str | None = Header(default=None)
    ) -> dict:
        """This account's instance, renting one if it has none. Safe to call repeatedly, and
        never blocks on the boot — poll it."""
        _guard(x_internal_key)
        try:
            return _view(service.ensure(_account(payload)))
        except BudgetExhausted as e:
            # 402 Payment Required, and the code matters: this is the one refusal here that a
            # retry will never fix, so it must not look like the transient ones below.
            raise HTTPException(status_code=402, detail=str(e)) from e
        except CapacityFull as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except NoOfferAvailable as e:
            # 503, not 502: nothing is broken, the market simply has nothing at this price.
            raise HTTPException(status_code=503, detail=str(e)) from e
        except (MarketplaceError, VastError) as e:
            raise HTTPException(status_code=502, detail=f"could not rent a GPU: {e}") from e

    @router.post("/vast/heartbeat")
    def heartbeat(
        payload: dict = Body(default={}), x_internal_key: str | None = Header(default=None)
    ) -> dict:
        """"Still using it." `lease_seconds` covers a submitted render, so a long job is not
        mistaken for an abandoned one; it is capped by the service."""
        _guard(x_internal_key)
        alive, row = service.heartbeat(
            _account(payload), float(payload.get("lease_seconds") or 0.0)
        )
        return {"alive": alive, **_view(row)}

    @router.get("/vast/status/{account_id}")
    def status(account_id: str, x_internal_key: str | None = Header(default=None)) -> dict:
        _guard(x_internal_key)
        return _view(service.status(account_id))

    @router.post("/vast/release")
    def release(
        payload: dict = Body(default={}), x_internal_key: str | None = Header(default=None)
    ) -> dict:
        """Give the machine back now rather than waiting for the idle sweep."""
        _guard(x_internal_key)
        released = service.release(_account(payload), reason="released by user")
        return {"released": released, **_view(None)}

    @router.get("/vast/spend/{account_id}")
    def spend(account_id: str, x_internal_key: str | None = Header(default=None)) -> dict:
        """What this account has spent on GPUs this month, and what is left."""
        _guard(x_internal_key)
        return service.spend_this_month(account_id)

    # ------------------------------------------------------------------ the clock

    @router.post("/vast/reap")
    def reap(x_internal_key: str | None = Header(default=None)) -> dict:
        """Kill what nobody wants any more. Called every minute by the scheduler, never by a user.

        NOT BEHIND `_guard`'s configured check. A deployment with no marketplace key has nothing
        to reap, and answering 503 every minute would fill the logs with an alarm-shaped
        non-event — the one thing guaranteed to get a real alarm ignored.
        """
        if not require_internal(x_internal_key):
            raise HTTPException(status_code=401, detail="internal key required")
        if not service.configured:
            return {"skipped": "no marketplace configured"}
        return reaper.sweep()

    @router.get("/vast/reaper")
    def reaper_status(x_internal_key: str | None = Header(default=None)) -> dict:
        """When the reaper last completed a sweep — the dead-man alarm reads this.

        `stale_seconds` is the number to alarm on. A reaper that has stopped shows zero kills,
        exactly like a quiet one; only the age of this timestamp tells them apart.
        """
        if not require_internal(x_internal_key):
            raise HTTPException(status_code=401, detail="internal key required")
        last = reaper.last_sweep()
        return {
            "last_sweep_at": last,
            "stale_seconds": None if not last else max(0.0, service.clock() - last),
            "ever_run": bool(last),
        }

    return router
