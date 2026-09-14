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
    resolve_bearer: Callable[[str], str | None],
) -> APIRouter:
    router = APIRouter()

    def _bearer_of(authorization: str | None) -> str:
        a = (authorization or "").strip()
        return a[len("Bearer "):].strip() if a.startswith("Bearer ") else ""

    def _claimed(payload: dict) -> str:
        return str((payload or {}).get("account_id") or "").strip()

    def _caller(x_internal_key: str | None, authorization: str | None, claimed: str) -> str:
        """WHO IS SPENDING. Two kinds of caller, one rule each:

          * trusted infra — the internal key, as a header or as the bearer — speaks for any
            account it NAMES (`account_id` in the payload or the path), as it always has;
          * a person — their own access token as the bearer — speaks for themselves only. The
            account is the token's; a payload or path naming somebody else is refused.

        The second kind is what lets a DESKTOP daemon, which holds no server secret and must
        not, rent a machine for the person signed in to it. Resolution of the token is the
        host's (`resolve_bearer`), the same check its other user-facing routes use.
        """
        if not service.configured:
            # No credentials on this deployment. 501, NOT 503: 503 is this router's word for
            # "temporary — ask again in a minute", and the window polls on it. A deployment
            # with no key would poll forever. 501 is what this is: the feature does not exist
            # on this server.
            raise HTTPException(
                status_code=501, detail="GPU renting is not configured on this deployment"
            )
        bearer = _bearer_of(authorization)
        if require_internal(x_internal_key) or (bearer and require_internal(bearer)):
            if not claimed:
                raise HTTPException(status_code=400, detail="account_id required")
            return claimed
        if not bearer:
            raise HTTPException(status_code=401, detail="internal key or bearer token required")
        account = resolve_bearer(bearer) or ""
        if not account:
            raise HTTPException(status_code=401, detail="invalid or expired token")
        if claimed and claimed != account:
            raise HTTPException(status_code=403, detail="not your account")
        return account

    @router.post("/vast/ensure")
    def ensure(
        payload: dict = Body(default={}),
        x_internal_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """This account's instance, renting one if it has none. Safe to call repeatedly, and
        never blocks on the boot — poll it."""
        account = _caller(x_internal_key, authorization, _claimed(payload))
        try:
            return _view(service.ensure(account))
        except BudgetExhausted as e:
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
        payload: dict = Body(default={}),
        x_internal_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """"Still using it." `lease_seconds` covers a submitted render, so a long job is not
        mistaken for an abandoned one; it is capped by the service."""
        account = _caller(x_internal_key, authorization, _claimed(payload))
        alive, row = service.heartbeat(account, float(payload.get("lease_seconds") or 0.0))
        return {"alive": alive, **_view(row)}

    @router.post("/vast/download-connection")
    def download_connection(
        payload: dict = Body(default={}),
        x_internal_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict:
        account = _caller(x_internal_key, authorization, _claimed(payload))
        try:
            return service.download_connection(account)
        except VastError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @router.post("/vast/idle")
    def idle(
        payload: dict = Body(default={}),
        x_internal_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """"Nobody is here." The daemon says so when an account's last window disconnects and
        when a run ends with no window watching; the reaper then counts ten minutes from the
        last real contact and does not let the box argue."""
        account = _caller(x_internal_key, authorization, _claimed(payload))
        marked, row = service.idle(account)
        return {"idle": marked, **_view(row)}

    @router.get("/vast/status/{account_id}")
    def status(
        account_id: str,
        x_internal_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict:
        account = _caller(x_internal_key, authorization, account_id)
        return _view(service.status(account))

    @router.post("/vast/release")
    def release(
        payload: dict = Body(default={}),
        x_internal_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Give the machine back now rather than waiting for the idle sweep."""
        account = _caller(x_internal_key, authorization, _claimed(payload))
        released = service.release(account, reason="released by user")
        return {"released": released, **_view(None)}

    @router.get("/vast/spend/{account_id}")
    def spend(
        account_id: str,
        x_internal_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """What this account has spent on GPUs this month, and what is left."""
        account = _caller(x_internal_key, authorization, account_id)
        return service.spend_this_month(account)

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
