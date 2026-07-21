"""Platform accounts seam — resolve a connection's session token to an ACCOUNT, and meter that
account's model spend against the Accounts service (the State plane).

Default OFF: when no accounts URL is configured (every desktop/local install), the daemon has no
notion of accounts — connections authenticate with the single machine token exactly as today, and
`current_account` stays None. When ON (a hosted deployment), the connection gate resolves the
presented token to an account, pins it on `current_account` for the turn, and the run can check the
account's budget before spending and report cost after.

Mirrors infrastructure/llm/model_gateway.py: one process-wide setting, configured once at boot.
The URL comes from env (AGENTD_ACCOUNTS_URL) or config (accounts.api_base); env wins. There is no
secret here — the session token is the client's credential, resolved over the wire.

The account identity is carried by a contextvar so it reaches code deep in the model-call stack
(model_gateway, usage reporting) without threading a parameter through every layer. `_run` sets it
at the top of the turn task; `asyncio.create_task` snapshots the context, so each run is isolated.
"""

from __future__ import annotations

import contextvars
import logging
import time

import httpx

log = logging.getLogger("agentd.accounts")

_enabled = False
_api_base = ""
_client: httpx.AsyncClient | None = None

# token -> (account dict, expiry epoch). A short TTL absorbs reconnect storms without letting a
# revoked token linger. Cleared on configure().
_resolve_cache: dict[str, tuple[dict, float]] = {}
_RESOLVE_TTL = 30.0

# The account behind the CURRENT turn (or None outside a scoped turn / when accounts are off).
# Shape: {"account_id", "email", "budget_usd", "over", ...} exactly as /resolve returns.
current_account: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "agentd_current_account", default=None
)

# Per-turn spend accumulator (a MUTABLE dict shared down the call stack by contextvar reference):
# every model call in the turn adds its tokens+cost here, and `_run` reports the total once when
# the turn ends. None outside an account-scoped turn (desktop/local => zero overhead). asyncio's
# create_task + to_thread both snapshot the context, so streaming brain calls AND threaded oneshot
# (vision) calls made during the turn all accrue to the same dict.
current_usage: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "agentd_current_usage", default=None
)

# Fallback price for models litellm can't price (so the ledger still accrues). Order-of-magnitude
# only; real pricing (when litellm knows the model) always wins.
_FALLBACK_IN_PER_TOKEN = 1.0 / 1_000_000  # $1 / 1M input tokens
_FALLBACK_OUT_PER_TOKEN = 3.0 / 1_000_000  # $3 / 1M output tokens


def start_usage():
    """Begin a fresh per-turn accumulator; returns the contextvar token to reset with."""
    return current_usage.set({"in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0, "model": ""})


def reset_usage(token) -> None:
    if token is None:  # start_usage was skipped (non-account turn) — nothing to reset
        return
    try:
        current_usage.reset(token)
    except (ValueError, LookupError):
        pass


def read_usage() -> dict | None:
    return current_usage.get()


def estimate_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    """Dollar cost of one call. Uses litellm's own pricing for the UNDERLYING model (the
    `litellm_proxy/` prefix, if any, is stripped); falls back to a nominal rate for unpriced
    models so spend never silently reads as zero."""
    base = (model or "").split("litellm_proxy/", 1)[-1]
    try:
        import litellm

        pc, cc = litellm.cost_per_token(
            model=base, prompt_tokens=int(in_tokens or 0), completion_tokens=int(out_tokens or 0)
        )
        cost = float(pc or 0.0) + float(cc or 0.0)
        if cost > 0:
            return cost
    except Exception:  # noqa: BLE001 — unknown model / pricing table miss -> fall back
        pass
    return int(in_tokens or 0) * _FALLBACK_IN_PER_TOKEN + int(out_tokens or 0) * _FALLBACK_OUT_PER_TOKEN


def add_usage(model: str, in_tokens: int, out_tokens: int) -> None:
    """Fold one model call's tokens+cost into the current turn's accumulator. Fast no-op outside
    an account-scoped turn (the common desktop/local path) — one contextvar read, nothing else."""
    acc = current_usage.get()
    if acc is None:
        return
    acc["in_tokens"] += int(in_tokens or 0)
    acc["out_tokens"] += int(out_tokens or 0)
    acc["cost_usd"] += estimate_cost(model, in_tokens, out_tokens)
    if model:
        acc["model"] = model


def configure(config) -> None:
    """Read the accounts settings once, at boot. Env overrides config; disabled unless a URL is
    present. Safe to call again (resets the client + cache)."""
    global _enabled, _api_base, _client
    import os

    acc = getattr(config, "accounts", None) or {}
    url = (os.environ.get("AGENTD_ACCOUNTS_URL") or str(acc.get("api_base") or "")).strip()
    _api_base = url.rstrip("/")
    _enabled = bool(_api_base) and (
        os.environ.get("AGENTD_ACCOUNTS_URL") is not None or bool(acc.get("enabled"))
    )
    _resolve_cache.clear()
    _client = httpx.AsyncClient(base_url=_api_base, timeout=5.0) if _enabled else None
    if _enabled:
        log.info("accounts: ON (%s)", _api_base)


def enabled() -> bool:
    return _enabled


def account_id() -> str | None:
    """The current turn's account id, or None (accounts off / unscoped call)."""
    acc = current_account.get()
    return acc.get("account_id") if acc else None


def set_account(account: dict | None):
    """Pin the account for the current context; returns the contextvar token to reset with."""
    return current_account.set(account)


def reset_account(token) -> None:
    try:
        current_account.reset(token)
    except (ValueError, LookupError):  # different context (task boundary) — best-effort
        pass


async def resolve(token: str) -> dict | None:
    """Session token -> account dict (or None if unknown/expired). Cached briefly. Never raises:
    a resolve failure (service down, bad token) is a None, which the caller treats as unauthorized."""
    if not _enabled or not token or _client is None:
        return None
    now = time.time()
    hit = _resolve_cache.get(token)
    if hit and hit[1] > now:
        return hit[0]
    try:
        r = await _client.get("/resolve", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        log.warning("accounts resolve failed: %s", e)
        return None
    if r.status_code != 200:
        return None
    try:
        acc = r.json()
    except ValueError:
        return None
    _resolve_cache[token] = (acc, now + _RESOLVE_TTL)
    return acc


async def check_budget(acct_id: str) -> dict | None:
    """Current month-to-date budget view for an account, or None on failure (fail-open: the
    caller decides, but a metering outage must not silently block a paying user)."""
    if not _enabled or not acct_id or _client is None:
        return None
    try:
        r = await _client.get(f"/budget/{acct_id}")
        if r.status_code == 200:
            return r.json()
    except httpx.HTTPError as e:
        log.warning("accounts budget check failed: %s", e)
    return None


async def report_usage(
    acct_id: str, model: str, in_tokens: int, out_tokens: int, cost_usd: float
) -> dict | None:
    """Append one model call's cost to the account's ledger. Best-effort — a reporting failure
    is logged but never breaks the run."""
    if not _enabled or not acct_id or _client is None:
        return None
    try:
        r = await _client.post(
            "/usage",
            json={
                "account_id": acct_id,
                "model": model,
                "in_tokens": int(in_tokens or 0),
                "out_tokens": int(out_tokens or 0),
                "cost_usd": float(cost_usd or 0.0),
            },
        )
        if r.status_code == 200:
            return r.json()
        log.warning("accounts usage report http %s", r.status_code)
    except httpx.HTTPError as e:
        log.warning("accounts usage report failed: %s", e)
    return None
