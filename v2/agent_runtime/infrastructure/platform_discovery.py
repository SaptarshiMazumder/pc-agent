"""Resolve the platform's addresses from ONE baked URL.

THE PROBLEM THIS REPLACES. A build used to bake four independent ALB hostnames (accounts, model
proxy, registry, ingest). Every one of them carries an AWS-assigned suffix that changes on any
destroy/recreate, so each release froze another set — which is how the shipped artifacts under
``clients/desktop/dist/`` ended up pointing at three different long-dead load balancers, and how
"the same email" could become two different accounts with two different credit balances depending
on which installer you happened to have.

``sync-platform-urls.mjs`` re-derives those keys from terraform, but nothing forces it to run
before a build. Fetching instead of baking removes the class of bug rather than the instance:
there is one address in the build, and everything else is whatever the deployment says today.

FAIL SOFT, ALWAYS. A daemon must start on a plane. So:
  * a successful fetch is cached to disk and reused,
  * a failed fetch falls back to the last good cache,
  * and then to the per-service keys the profile still carries,
  * and a build with no ``platform_url`` at all never calls this and behaves exactly as before.

Sync (not async) and called once at boot, because ``accounts.configure`` and
``model_proxy.configure`` are themselves synchronous boot-time calls. The timeout is short for the
same reason: a slow discovery endpoint must delay startup by seconds, not minutes.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("agentd.platform")

WELL_KNOWN = "/.well-known/agentd-platform"

#: How long a cached document is served without re-fetching. Long enough that a daemon restart
#: loop does not hammer the endpoint; short enough that moving a service is picked up the same day.
CACHE_TTL_S = 6 * 3600
_TIMEOUT_S = 4.0

_memo: dict[str, dict] = {}


def _cache_file(state_dir) -> Path:
    return Path(state_dir) / "platform-discovery.json"


def _read_cache(state_dir, base: str) -> dict | None:
    try:
        raw = json.loads(_cache_file(state_dir).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("_base") != base:
        # A cache written for a DIFFERENT platform URL is not a fallback, it is the wrong
        # deployment's addresses. Discarding it is what keeps a re-pointed build from silently
        # continuing to talk to the old stack.
        return None
    return raw


def _write_cache(state_dir, base: str, doc: dict) -> None:
    try:
        path = _cache_file(state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**doc, "_base": base, "_at": time.time()}), "utf-8")
    except OSError:  # pragma: no cover — a read-only state dir must not break boot
        pass


def _fetch(base: str) -> dict | None:
    try:
        import httpx

        r = httpx.get(f"{base}{WELL_KNOWN}", timeout=_TIMEOUT_S)
        if r.status_code != 200:
            log.warning("platform discovery http %s from %s", r.status_code, base)
            return None
        doc = r.json()
        return doc if isinstance(doc, dict) else None
    except Exception as e:  # noqa: BLE001 — ANY failure here degrades to the cache/baked values
        log.warning("platform discovery failed (%s): %s", type(e).__name__, e or "no detail")
        return None


def resolve(config) -> dict:
    """The platform document for this install, or ``{}`` when there is nothing to discover.

    Memoised per platform URL for the life of the process: several seams ask for this at boot and
    none of them should cost a round trip.
    """
    profile = getattr(config, "distribution", None)
    base = str(getattr(profile, "platform_url", "") or "").strip().rstrip("/")
    # An explicit environment override wins, so a developer can point one daemon at a local stack
    # without editing a build artifact.
    base = (os.environ.get("AGENTD_PLATFORM_URL", "") or base).strip().rstrip("/")
    if not base:
        return {}
    if base in _memo:
        return _memo[base]

    state_dir = getattr(config, "state_dir", None) or "."
    cached = _read_cache(state_dir, base)
    if cached and (time.time() - float(cached.get("_at") or 0)) < CACHE_TTL_S:
        _memo[base] = cached
        return cached

    doc = _fetch(base)
    if doc is None:
        # Stale beats absent: yesterday's addresses are far more likely to be right than none.
        doc = cached or {}
        if doc:
            log.info("platform discovery: serving cached document for %s", base)
    else:
        _write_cache(state_dir, base, doc)
    _memo[base] = doc
    return doc


def field(config, name: str) -> str:
    """One URL from the discovered document ("" when absent or nothing is configured)."""
    return str(resolve(config).get(name) or "").strip().rstrip("/")


def reset() -> None:
    """Drop the in-process memo (tests, and a future live re-resolve)."""
    _memo.clear()
