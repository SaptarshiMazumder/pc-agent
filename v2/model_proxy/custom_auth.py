"""Model-proxy auth + metering — per-USER credentials at the LiteLLM proxy.

Mounted into the proxy via litellm.config.yaml:
    general_settings.custom_auth:  custom_auth.user_api_key_auth
    litellm_settings.callbacks:    custom_auth.usage_logger_instance

Two credential classes (custom_auth REPLACES the proxy's standard key check entirely):
  * the master key (env LITELLM_MASTER_KEY)  -> trusted infra (our cloud daemon, ops curls).
    Account attribution, when wanted, comes from the request's OpenAI `user` field.
  * anything else -> treated as an accounts SESSION TOKEN and resolved against the Accounts
    service (GET {ACCOUNTS_URL}/resolve). Valid -> the call runs on OUR provider keys,
    attributed to that account; invalid -> 401. This is how a signed-in DESKTOP daemon uses
    platform keys without any shared secret ever shipping in an installer.

Metering is PROXY-SIDE: the success callback posts each call's tokens+cost to the Accounts
ledger (POST /usage, authenticated with ACCOUNTS_INTERNAL_KEY) — the proxy sees every call
server-side, so desktops can't misreport. Fire-and-forget: a ledger outage never fails a
model call. Swap-later seam: when per-account budgets need enforcement, replace this module
with LiteLLM virtual keys (+ Postgres) — clients just send a different string as their key.

Config is ENV-ONLY (no constants):
    LITELLM_MASTER_KEY     required — the infra credential
    ACCOUNTS_URL           required for session-token auth (else only the master key works)
    ACCOUNTS_INTERNAL_KEY  required for ledger writes (else usage logging is skipped)
    ACCOUNTS_RESOLVE_TTL   seconds a token->account resolution is cached (default 60)
    ACCOUNTS_TIMEOUT_S     accounts-service HTTP timeout (default 5)
    ACCOUNTS_RESOLVE_GRACE_S  seconds PAST expiry a cached resolution may serve when Accounts
                           is unreachable (default 900) — keeps long runs alive through blips;
                           a never-resolved token still fails closed
"""

from __future__ import annotations

import hmac
import logging
import os
import time
import uuid

import httpx
from fastapi import HTTPException, Request
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth

# metering.py is a SIBLING module. The bare import works when litellm loads us with the config
# file's directory on sys.path (the production path), but not when this file is loaded by path —
# which tests do, and which is also how a future embedder might. Fall back to loading it
# explicitly from next to this file so the module is self-sufficient under any loader.
try:
    import metering
except ModuleNotFoundError:  # pragma: no cover - exercised by tests loading us by path
    import importlib.util as _ilu
    import pathlib as _pathlib

    _spec = _ilu.spec_from_file_location(
        "metering", _pathlib.Path(__file__).with_name("metering.py")
    )
    if _spec is None or _spec.loader is None:  # pragma: no cover
        raise
    metering = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(metering)

# Shared instrumentation (v2/monitoring). Optional at import time ONLY so an image that has not
# installed it yet still boots — a metrics package must never be able to take the proxy down.
# When absent, every call below is a no-op and `enabled` stays False (visible in the boot log).
try:
    from agentd_telemetry import bind as _bind
    from agentd_telemetry import count, gauge, money, scope, setup_logging, timing

    _TELEMETRY = True
except ImportError:  # pragma: no cover
    _TELEMETRY = False

    def _bind(**_k):  # type: ignore[misc]
        return None

    def count(*_a, **_k):  # type: ignore[misc]
        pass

    def gauge(*_a, **_k):  # type: ignore[misc]
        pass

    def money(*_a, **_k):  # type: ignore[misc]
        pass

    def timing(*_a, **_k):  # type: ignore[misc]
        pass

    def setup_logging(*_a, **_k):  # type: ignore[misc]
        pass

    from contextlib import nullcontext

    def scope(**_k):  # type: ignore[misc]
        return nullcontext()


log = logging.getLogger("model_proxy.auth")

setup_logging("model-proxy")
log.info("model_proxy telemetry %s", "ENABLED" if _TELEMETRY else "DISABLED (agentd_telemetry not installed)")

# token -> (account_id, expiry epoch). Short TTL: absorbs per-turn call bursts without letting
# a revoked/expired session linger at the proxy.
_resolve_cache: dict[str, tuple[str, float]] = {}

# (account_id, agent_id) -> (funding view, expiry). Same reasoning as the resolve cache: this is
# read before every uncached model call, so it must not be a round trip every time.
_funding_cache: dict[tuple[str, str], tuple[dict, float]] = {}

# fallback pricing for models litellm can't price — mirrors agentd's accounts.estimate_cost
# so the ledger never silently reads zero (order-of-magnitude only; real pricing wins).
_FALLBACK_IN_PER_TOKEN = 1.0 / 1_000_000  # $1 / 1M input tokens
_FALLBACK_OUT_PER_TOKEN = 3.0 / 1_000_000  # $3 / 1M output tokens

_client: httpx.AsyncClient | None = None


def _bind_trace(request: Request) -> dict:
    """Lift the correlation headers agentd's model_proxy.apply() attached onto this request.

    Returns the same values so the usage post can forward them to the ledger — the last hop
    that turns three independent services into one searchable story.
    """
    try:
        headers = request.headers
    except Exception:  # noqa: BLE001 — never let telemetry break auth
        return {}
    trace = {
        "run_id": (headers.get("x-agentd-run-id") or "")[:64],
        "turn_id": (headers.get("x-agentd-turn-id") or "")[:80],
        "agent_id": (headers.get("x-agentd-agent-id") or "")[:64],
    }
    trace = {k: v for k, v in trace.items() if v}
    if trace:
        _bind(**trace)
    return trace


def _extract_trace(headers) -> dict:
    """Pull our correlation headers out of one header mapping, if they are there."""
    if not isinstance(headers, dict):
        return {}
    lower = {str(k).lower(): v for k, v in headers.items()}
    run_id = str(lower.get("x-agentd-run-id") or "")[:64]
    agent_id = str(lower.get("x-agentd-agent-id") or "")[:64]
    if not run_id and not agent_id:
        return {}
    return {
        "run_id": run_id,
        "turn_id": str(lower.get("x-agentd-turn-id") or "")[:80],
        "agent_id": agent_id,
    }


def _header_candidates(container: dict) -> tuple:
    """Everywhere LiteLLM might have stashed the incoming request headers.

    WHERE it keeps them has moved between releases, and the pre-call hook and the success
    callback are handed differently-shaped dicts. Probing several known shapes means an upgrade
    degrades to "no run_id on this row" instead of silently breaking correlation everywhere.
    """
    params = container.get("litellm_params") or {}
    metadata = params.get("metadata") or container.get("metadata") or {}
    return (
        metadata.get("headers"),
        (metadata.get("proxy_server_request") or {}).get("headers"),
        (container.get("proxy_server_request") or {}).get("headers"),
    )


def _trace_from_kwargs(kwargs: dict) -> dict:
    """Recover the correlation ids inside the success callback.

    The callback does not necessarily run in the auth hook's task context, so the contextvar
    set by `_bind_trace` may not be visible here. Empty dict is fine: the ledger row simply
    has no run_id, exactly as it did before correlation existed.
    """
    for headers in _header_candidates(kwargs or {}):
        trace = _extract_trace(headers)
        if trace:
            return trace
    return {}


def _trace_from_data(data: dict) -> dict:
    """Same, for the pre-call hook's `data` dict — where we need agent_id to pick the right
    funding scope (an agent's own bundled allowance vs the shared platform pool)."""
    for headers in _header_candidates(data or {}):
        trace = _extract_trace(headers)
        if trace:
            return trace
    return {}


def _accounts_client() -> httpx.AsyncClient | None:
    """Lazy shared client (created on the running loop, reused across requests)."""
    global _client
    url = os.environ.get("ACCOUNTS_URL", "").strip().rstrip("/")
    if not url:
        return None
    if _client is None:
        timeout = float(os.environ.get("ACCOUNTS_TIMEOUT_S", "5") or 5)
        _client = httpx.AsyncClient(base_url=url, timeout=timeout)
    return _client


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    """The proxy's auth gate. Master key => trusted infra; else the key IS an accounts
    session token, resolved (and briefly cached) against the Accounts service."""
    # Pick up the caller's tracking number and hold it for this request, so every line the
    # proxy writes about this call — auth, cost, ledger — carries the same id the daemon and
    # the client used. `bind` (not `scope`) because FastAPI handles each request in its own
    # task context, so the binding cannot leak into a concurrent request.
    _bind_trace(request)

    key = (api_key or "").strip()
    master = os.environ.get("LITELLM_MASTER_KEY", "").strip()
    if master and hmac.compare_digest(key, master):
        count("auth_total", credential="master", outcome="ok")
        return UserAPIKeyAuth(api_key=key)

    client = _accounts_client()
    if not key or client is None:
        count("auth_total", credential="none", outcome="rejected")
        raise Exception("invalid credentials: sign in required")

    now = time.time()
    hit = _resolve_cache.get(key)
    if hit and hit[1] > now:
        # Cache hit rate matters: a miss puts a synchronous Accounts round-trip in front of the
        # model call, so this ratio is the difference between a fast turn and a stalled one.
        count("auth_total", credential="session", outcome="ok", cache="hit")
        return UserAPIKeyAuth(api_key=key, user_id=hit[0])

    started = time.perf_counter()
    r = None
    error: httpx.HTTPError | None = None
    # ONE retry, immediately: the common failure is a single dropped/timed-out round trip (a
    # keep-alive the far side closed, an EFS latency spike inside Accounts), and a long agent
    # run crosses the cache TTL many times — each crossing used to be a single unlucky socket
    # away from killing the whole run.
    for _attempt in range(2):
        try:
            r = await client.get("/resolve", headers={"Authorization": f"Bearer {key}"})
            error = None
            break
        except httpx.HTTPError as e:
            error = e
    if error is not None:
        timing("resolve_latency_ms", (time.perf_counter() - started) * 1000, outcome="unreachable")
        # STALE-GRACE. This token resolved fine moments ago; an Accounts blip does not change
        # whose token it is. Serving the expired entry (bounded by the grace window) keeps a
        # paying user's run alive through a hiccup — the same fail-open trade the funding
        # lookup already makes, bounded instead of blanket. The real cost is honest and small:
        # a REVOKED session can outlive its revocation by up to the grace window, and only
        # while Accounts is down. A token we have NEVER resolved still fails closed: there is
        # nothing safe to fall back to.
        grace = float(os.environ.get("ACCOUNTS_RESOLVE_GRACE_S", "900") or 900)
        if hit and hit[1] + grace > now:
            count("auth_total", credential="session", outcome="ok", cache="stale-grace")
            log.warning("accounts resolve unreachable — serving stale resolution: %s", error)
            return UserAPIKeyAuth(api_key=key, user_id=hit[0])
        # Accounts is a HARD dependency in the hot path of every model call (plan DEF-6):
        # when it is unreachable, every NEW token is blocked within the cache TTL.
        count("auth_total", credential="session", outcome="unavailable")
        log.warning("accounts resolve unreachable: %s", error)
        raise Exception("account service unavailable") from error
    timing(
        "resolve_latency_ms",
        (time.perf_counter() - started) * 1000,
        outcome="ok" if r.status_code == 200 else "rejected",
    )
    if r.status_code != 200:
        count("auth_total", credential="session", outcome="rejected", _props={"status_code": r.status_code})
        raise Exception("invalid credentials: sign in required")
    account_id = str((r.json() or {}).get("account_id") or "")
    if not account_id:
        count("auth_total", credential="session", outcome="rejected", _props={"reason": "no_account_id"})
        raise Exception("invalid credentials: sign in required")
    ttl = float(os.environ.get("ACCOUNTS_RESOLVE_TTL", "60") or 60)
    _resolve_cache[key] = (account_id, now + ttl)
    count("auth_total", credential="session", outcome="ok", cache="miss")
    return UserAPIKeyAuth(api_key=key, user_id=account_id)


def _cached_tokens(usage) -> int:
    """Cache-read tokens, if the provider reported any.

    Worth its own row column: a cache read costs ~10% of a normal input token, so the
    cached/input ratio is the single biggest lever on cost of goods — and agents have very
    large, very stable system prompts. Providers disagree on where they put this, hence the
    probing; absent means 0, which is exactly what it meant before.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        value = getattr(details, "cached_tokens", None)
        if value is None and isinstance(details, dict):
            value = details.get("cached_tokens")
        if value:
            return int(value)
    if isinstance(usage, dict):
        nested = usage.get("prompt_tokens_details") or {}
        if isinstance(nested, dict) and nested.get("cached_tokens"):
            return int(nested["cached_tokens"])
        if usage.get("cache_read_input_tokens"):  # anthropic-style
            return int(usage["cache_read_input_tokens"])
    return int(getattr(usage, "cache_read_input_tokens", 0) or 0)


def _usage_fields(kwargs: dict, response_obj) -> tuple[str, int, int, float, int]:
    """(model, in_tokens, out_tokens, cost_usd, cached_tokens) for one completed call."""
    model = str(kwargs.get("model") or "")
    usage = getattr(response_obj, "usage", None) or {}
    if hasattr(usage, "prompt_tokens"):
        in_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    else:
        in_tokens = int(usage.get("prompt_tokens") or 0)
        out_tokens = int(usage.get("completion_tokens") or 0)
    cost = kwargs.get("response_cost")
    if not cost:
        cost = in_tokens * _FALLBACK_IN_PER_TOKEN + out_tokens * _FALLBACK_OUT_PER_TOKEN
    return model, in_tokens, out_tokens, float(cost), _cached_tokens(usage)


async def _funding(account_id: str, agent_id: str) -> dict | None:
    """This account's spendable credits + tier ceiling, briefly cached.

    Cached because it sits on the same hot path as /resolve — one extra synchronous round trip
    per model call is a real cost to every user's every message. A short TTL means an exhausted
    balance is noticed within seconds, which is the right trade for a cap whose job is to stop
    runaway spend rather than to be exact to the credit.
    """
    internal = os.environ.get("ACCOUNTS_INTERNAL_KEY", "").strip()
    client = _accounts_client()
    if not internal or client is None or not account_id:
        return None
    now = time.time()
    hit = _funding_cache.get((account_id, agent_id))
    if hit and hit[1] > now:
        return hit[0]
    try:
        r = await client.get(
            "/funding",
            params={"account_id": account_id, "agent_id": agent_id},
            headers={"X-Internal-Key": internal},
        )
    except httpx.HTTPError as e:
        # Fail OPEN. A metering outage must not stop a paying customer working; the ledger and
        # its alarm are what catch the resulting drift. The alternative — deny on error — turns
        # any Accounts blip into a total outage for every user.
        count("funding_lookup_total", outcome="unavailable")
        log.warning("funding lookup failed for %s: %s", account_id, e)
        return None
    if r.status_code != 200:
        count("funding_lookup_total", outcome="error", _props={"status_code": r.status_code})
        return None
    view = r.json() or {}
    ttl = float(os.environ.get("AGENTD_FUNDING_TTL", "10") or 10)
    _funding_cache[(account_id, agent_id)] = (view, now + ttl)
    count("funding_lookup_total", outcome="ok")
    return view


def _invalidate_funding(account_id: str) -> None:
    """Drop cached funding for an account after a debit, so the cap tightens immediately."""
    for key in [k for k in _funding_cache if k[0] == account_id]:
        _funding_cache.pop(key, None)


class AccountsUsageLogger(CustomLogger):
    """Post every successful call's tokens+cost to the Accounts ledger, keyed by the account
    the auth gate attached (session-token callers) or the request's `user` field (master-key
    callers naming an account). No account => nothing to meter (ops curls)."""

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        """THE GATE. Runs before the provider is touched, which is the only useful place for it.

        Closes plan DEF-2: budgets were enforced NOWHERE for desktop Cloud users. The daemon's
        own check requires accounts-mode, which is false on every desktop, and the proxy — the
        one chokepoint both desktop and hosted traffic must cross — never checked at all. So a
        signed-in desktop user could spend past their limit indefinitely.

        Two refusals, both BEFORE any money is spent:
          * no credits left  -> 402. Hard stop, no overdraft (that is what makes worst-case
            cost knowable, and therefore what makes same-day creator payouts safe).
          * model too expensive for this grant -> 403. Aimed at free/promotional allowances,
            whose creators have no cost incentive of their own.

        Returns None to allow. Raising an HTTPException here blocks the call.
        """
        account_id = str(getattr(user_api_key_dict, "user_id", "") or "").strip()
        if not account_id:
            return None  # master-key infra / ops calls carry no account to meter
        agent_id = str((_trace_from_data(data) or {}).get("agent_id") or "")
        view = await _funding(account_id, agent_id)
        if view is None:
            return None  # metering unavailable -> fail open (see _funding)

        model = str((data or {}).get("model") or "")
        remaining = int(view.get("credits_remaining") or 0)
        tier_max = str(view.get("model_tier_max") or "")

        # ENTITLEMENT (plan 2.5), checked before credits: "you may not run this" is a clearer
        # answer than "you are out of credits" for someone who never bought the agent, and it
        # avoids telling a non-buyer anything about their balance. Rides the funding response, so
        # no extra round trip. `entitlement_required` is false unless an active product names
        # this agent, which is what keeps first-party and off-marketplace agents ungated. An
        # older accounts build omits both fields -> absent means allowed, so this fails OPEN.
        if view.get("entitlement_required") and not view.get("entitled", True):
            count(
                "run_refused_total", reason="not_entitled",
                _props={"account_id": account_id, "agent_id": agent_id},
            )
            raise HTTPException(
                status_code=403,
                detail=f"no active subscription for agent '{agent_id}'",
            )

        if remaining <= 0:
            count("run_refused_total", reason="no_credits", _props={"account_id": account_id})
            raise HTTPException(
                status_code=402,
                detail="out of credits — top up to keep going",
            )
        if not metering.tier_allowed(model, tier_max):
            count(
                "run_refused_total", reason="model_tier",
                _props={"account_id": account_id, "model": model, "model_tier": tier_max},
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    f"model '{model}' is above this plan's '{tier_max}' tier — "
                    "upgrade, or use an agent configured for cheaper models"
                ),
            )
        return None

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        internal = os.environ.get("ACCOUNTS_INTERNAL_KEY", "").strip()
        client = _accounts_client()
        metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
        account_id = str(
            metadata.get("user_api_key_user_id")
            or kwargs.get("user")
            or (kwargs.get("optional_params") or {}).get("user")
            or ""
        ).strip()
        model, in_tokens, out_tokens, cost, cached = _usage_fields(kwargs, response_obj)
        trace = _trace_from_kwargs(kwargs)

        # Every successful call is recorded as a metric REGARDLESS of whether the ledger write
        # below succeeds. That separation is the point: the graph of what we SPENT must not
        # depend on the system that records what we BILLED, or an Accounts outage makes both
        # disappear together and the loss becomes invisible.
        # What we PAID the provider vs what we CHARGE the user. Both recorded; the gap is margin.
        charged = metering.credits_for(cost)

        with scope(account_id=account_id or None, **trace):
            money("model_cost_usd", cost, _props={"model": model})
            count("credits_charged_total", charged, _props={"model": model})
            count("model_call_total", outcome="ok", _props={"model": model})
            count("tokens_total", in_tokens, direction="in", _props={"model": model})
            count("tokens_total", out_tokens, direction="out", _props={"model": model})
            if cached:
                count("tokens_total", cached, direction="cached", _props={"model": model})

            if not internal or client is None:
                count("ledger_write_total", outcome="skipped", reason="not_configured")
                return
            if not account_id:
                # An unattributed call: real money spent that can never be billed to anyone.
                count("ledger_write_total", outcome="skipped", reason="no_account")
                money("unbilled_cost_usd", cost, _props={"model": model})
                return

            # DEBIT FIRST, then record. If only one of the two can happen, the balance is the
            # one that must move: a missing usage row is a reporting gap the buffer below can
            # replay, but a missing debit is free compute that the cap was supposed to stop.
            funding_source = ""
            try:
                d = await client.post(
                    "/debit",
                    headers={"X-Internal-Key": internal},
                    json={
                        "account_id": account_id,
                        "agent_id": trace.get("agent_id", ""),
                        "credits": charged,
                        "run_id": trace.get("run_id", ""),
                    },
                )
                if d.status_code == 200:
                    body = d.json() or {}
                    funding_source = str(body.get("funding_source") or "")
                    gauge("credits_remaining", int(body.get("credits_remaining") or 0))
                    shortfall = int(body.get("shortfall") or 0)
                    if shortfall > 0:
                        # The balance covered only part of this call: accounts DRAINED it to zero
                        # (so the pre-call gate closes on the very next call) and reported what
                        # went uncovered. The overspend is the uncovered FRACTION of the cost,
                        # not the whole call — the drained part was paid for.
                        count("debit_applied_total", outcome="drained")
                        money(
                            "overspend_usd",
                            cost * (shortfall / max(1, charged)),
                            _props={"model": model},
                        )
                    else:
                        count("debit_applied_total", outcome="ok")
                elif d.status_code == 402:
                    # Zero balance before this call even started billing — the pre-call gate let
                    # something through (a cache-window race). The whole call is unrecovered.
                    count("debit_applied_total", outcome="overspend")
                    money("overspend_usd", cost, _props={"model": model})
                else:
                    count("debit_applied_total", outcome="error",
                          _props={"status_code": d.status_code})
            except httpx.HTTPError as e:
                count("debit_applied_total", outcome="unreachable")
                log.warning("debit failed for %s: %s", account_id, e)
            _invalidate_funding(account_id)

            row = {
                # EXACTLY-ONCE KEY, minted HERE and never regenerated. The retry buffer below
                # replays this exact dict, and a failed write includes the case where Accounts
                # committed the row but the response was lost — so a fresh id per attempt would
                # let a replay insert a second copy of the same charge. Prefer LiteLLM's own
                # call id: it is stable for this call and survives a process restart in a way
                # a uuid minted here would not.
                "event_id": str(kwargs.get("litellm_call_id") or "") or uuid.uuid4().hex,
                "account_id": account_id,
                "model": model,
                "model_tier": metering.tier_for(model),
                "in_tokens": in_tokens,
                "out_tokens": out_tokens,
                "cached_tokens": cached,
                "cost_usd": cost,
                "credits": charged,
                "funding_source": funding_source,
                # WHICH AGENT spent it — per-agent attribution, not just per-account
                "agent_id": trace.get("agent_id", ""),
                # the last hop of the tracking number: onto the money row itself
                "run_id": trace.get("run_id", ""),
                "turn_id": trace.get("turn_id", ""),
            }
            try:
                r = await client.post("/usage", headers={"X-Internal-Key": internal}, json=row)
                if r.status_code != 200:
                    count("ledger_write_total", outcome="fail", reason="http_error",
                          _props={"status_code": r.status_code})
                    gauge("ledger_buffer_depth", metering.buffer_row(row))
                    log.warning("usage post http %s for %s", r.status_code, account_id)
                else:
                    count("ledger_write_total", outcome="ok")
                    # Opportunistic recovery: a successful write proves Accounts is back, so
                    # drain the backlog now. No timer, no background task to supervise.
                    if metering.buffer_depth():
                        replayed, left = await metering.drain_buffer(client, internal)
                        if replayed:
                            count("ledger_write_total", replayed, outcome="replayed")
                        gauge("ledger_buffer_depth", left)
            except httpx.HTTPError as e:  # ledger outage must never fail the model call
                # DEF-1. Swallowing the exception is right — a billing outage must not break a
                # chat. What was missing: nobody COUNTED it and nobody REPLAYED it, so an hour
                # of unrecorded spend had no symptom and was unrecoverable. Now it is both
                # counted (the paging alarm watches this) and queued for replay.
                count("ledger_write_total", outcome="fail", reason="unreachable")
                money("unbilled_cost_usd", cost, _props={"model": model})
                gauge("ledger_buffer_depth", metering.buffer_row(row))
                log.warning("usage post failed for %s: %s", account_id, e)


usage_logger_instance = AccountsUsageLogger()
