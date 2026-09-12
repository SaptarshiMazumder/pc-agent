"""Composition root: the one place the pieces are wired, and the one place env vars are read.

EVERYTHING CONFIGURABLE IS READ HERE. Layers below take their settings as arguments, so a test
constructs them directly and nothing deep in the module quietly consults os.environ. That is
also what keeps the host's contact surface to three calls — see vast/__init__.py.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from vast.application.instance_settings import InstanceSettings
from vast.application.services.instance_reaper import InstanceReaper
from vast.application.services.instance_service import InstanceService
from vast.infrastructure.sql_instance_store import SqlInstanceStore
from vast.infrastructure.vast_marketplace import VastMarketplace

#: Secret names this module needs. The host adds these to whatever it already loads, so the
#: module declares its own credentials instead of the host knowing about them.
#:
#: ABSENT IS A VALID STATE: no key means this deployment rents nothing and /vast/* answers 503.
#: That is the correct posture for desktop and for any environment not given a key, and it is
#: why nothing here raises when the name is missing.
SECRET_FIELDS: tuple[str, ...] = ("VAST_API_KEY",)


def settings_from_env() -> InstanceSettings:
    """Deployment policy, with defaults that are safe rather than generous."""
    return InstanceSettings(
        # The default lives on InstanceSettings, with the note on why a floating tag is not
        # safe here. Duplicating it meant one place got fixed and the other kept renting
        # machines to pull an image that does not exist.
        image=os.environ.get("VAST_IMAGE", "").strip() or InstanceSettings.image,
        disk_gb=int(os.environ.get("VAST_DISK_GB", "") or 60),
        comfy_port=int(os.environ.get("VAST_COMFY_PORT", "") or 8188),
        max_hourly_usd=float(
            os.environ.get("VAST_MAX_HOURLY_USD", "") or InstanceSettings.max_hourly_usd
        ),
        # "0" / "false" / "no" turn it off; anything else (and unset) keeps Secure Cloud only.
        secure_cloud_only=(os.environ.get("VAST_SECURE_CLOUD_ONLY", "").strip().lower()
                           not in ("0", "false", "no")),
        failed_machine_cooldown_seconds=float(
            os.environ.get("VAST_FAILED_MACHINE_COOLDOWN_SECONDS", "")
            or InstanceSettings.failed_machine_cooldown_seconds
        ),
        min_vram_gb=int(os.environ.get("VAST_MIN_VRAM_GB", "") or InstanceSettings.min_vram_gb),
        min_compute_cap=int(
            os.environ.get("VAST_MIN_COMPUTE_CAP", "") or InstanceSettings.min_compute_cap
        ),
        # Off unless a deployment says so; see InstanceSettings.require_verified.
        require_verified=(os.environ.get("VAST_REQUIRE_VERIFIED", "").strip().lower()
                          in ("1", "true", "yes")),
        idle_seconds=float(os.environ.get("VAST_IDLE_SECONDS", "") or 600),
        monthly_cap_usd=float(os.environ.get("VAST_MONTHLY_CAP_USD", "") or 20.0),
        max_live_instances=int(os.environ.get("VAST_MAX_LIVE_INSTANCES", "") or 10),
    )


def build_service(
    *,
    db: Callable[[], AbstractContextManager[Any]],
    now: Callable[[], float],
    settings: InstanceSettings | None = None,
) -> InstanceService:
    """The service, wired to Vast and to the host's database."""
    cfg = settings or settings_from_env()
    return InstanceService(
        db=db,
        store=SqlInstanceStore(),
        # A FACTORY, deliberately. This runs at import time while the API key arrives later from
        # the secret store; a client built now would capture an empty key and report "not
        # configured" forever on a perfectly good deployment. Building one per call costs
        # nothing — it holds a string, not a connection.
        marketplace=lambda: VastMarketplace(
            os.environ.get("VAST_API_KEY", "").strip(), comfy_port=cfg.comfy_port
        ),
        settings=cfg,
        now=now,
    )


def build_reaper(
    *,
    db: Callable[[], AbstractContextManager[Any]],
    now: Callable[[], float],
    settings: InstanceSettings | None = None,
) -> InstanceReaper:
    """The sweep, wired to the same marketplace and table the service uses.

    SAME MARKETPLACE FACTORY, deliberately: the thing that kills must be the thing that creates,
    or the two end up disagreeing about what a dead instance looks like.
    """
    cfg = settings or settings_from_env()
    return InstanceReaper(
        db=db,
        store=SqlInstanceStore(),
        marketplace=lambda: VastMarketplace(
            os.environ.get("VAST_API_KEY", "").strip(), comfy_port=cfg.comfy_port
        ),
        settings=cfg,
        now=now,
    )
