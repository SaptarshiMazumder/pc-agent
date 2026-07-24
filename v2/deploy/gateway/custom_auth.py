"""Model-gateway auth + metering — per-USER credentials at the LiteLLM proxy.

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
"""

from __future__ import annotations

import hmac
import logging
import os
import time

import httpx
from fastapi import Request
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth

log = logging.getLogger("gateway.auth")

# token -> (account_id, expiry epoch). Short TTL: absorbs per-turn call bursts without letting
# a revoked/expired session linger at the proxy.
_resolve_cache: dict[str, tuple[str, float]] = {}

# fallback pricing for models litellm can't price — mirrors agentd's accounts.estimate_cost
# so the ledger never silently reads zero (order-of-magnitude only; real pricing wins).
_FALLBACK_IN_PER_TOKEN = 1.0 / 1_000_000  # $1 / 1M input tokens
_FALLBACK_OUT_PER_TOKEN = 3.0 / 1_000_000  # $3 / 1M output tokens

_client: httpx.AsyncClient | None = None


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
    key = (api_key or "").strip()
    master = os.environ.get("LITELLM_MASTER_KEY", "").strip()
    if master and hmac.compare_digest(key, master):
        return UserAPIKeyAuth(api_key=key)

    client = _accounts_client()
    if not key or client is None:
        raise Exception("invalid credentials: sign in required")

    now = time.time()
    hit = _resolve_cache.get(key)
    if hit and hit[1] > now:
        return UserAPIKeyAuth(api_key=key, user_id=hit[0])

    try:
        r = await client.get("/resolve", headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        log.warning("accounts resolve unreachable: %s", e)
        raise Exception("account service unavailable") from e
    if r.status_code != 200:
        raise Exception("invalid credentials: sign in required")
    account_id = str((r.json() or {}).get("account_id") or "")
    if not account_id:
        raise Exception("invalid credentials: sign in required")
    ttl = float(os.environ.get("ACCOUNTS_RESOLVE_TTL", "60") or 60)
    _resolve_cache[key] = (account_id, now + ttl)
    return UserAPIKeyAuth(api_key=key, user_id=account_id)


def _usage_fields(kwargs: dict, response_obj) -> tuple[str, int, int, float]:
    """(model, in_tokens, out_tokens, cost_usd) for one completed call."""
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
    return model, in_tokens, out_tokens, float(cost)


class AccountsUsageLogger(CustomLogger):
    """Post every successful call's tokens+cost to the Accounts ledger, keyed by the account
    the auth gate attached (session-token callers) or the request's `user` field (master-key
    callers naming an account). No account => nothing to meter (ops curls)."""

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        internal = os.environ.get("ACCOUNTS_INTERNAL_KEY", "").strip()
        client = _accounts_client()
        if not internal or client is None:
            return
        metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
        account_id = str(
            metadata.get("user_api_key_user_id")
            or kwargs.get("user")
            or (kwargs.get("optional_params") or {}).get("user")
            or ""
        ).strip()
        if not account_id:
            return
        model, in_tokens, out_tokens, cost = _usage_fields(kwargs, response_obj)
        try:
            r = await client.post(
                "/usage",
                headers={"X-Internal-Key": internal},
                json={
                    "account_id": account_id,
                    "model": model,
                    "in_tokens": in_tokens,
                    "out_tokens": out_tokens,
                    "cost_usd": cost,
                },
            )
            if r.status_code != 200:
                log.warning("usage post http %s for %s", r.status_code, account_id)
        except httpx.HTTPError as e:  # ledger outage must never fail the model call
            log.warning("usage post failed for %s: %s", account_id, e)


usage_logger_instance = AccountsUsageLogger()
