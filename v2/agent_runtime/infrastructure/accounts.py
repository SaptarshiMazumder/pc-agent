"""Platform accounts seam — resolve a connection's session token to an ACCOUNT, and meter that
account's model spend against the Accounts service (the State plane).

Default OFF: when no accounts URL is configured (every desktop/local install), the daemon has no
notion of accounts — connections authenticate with the single machine token exactly as today, and
`current_account` stays None. When ON (a hosted deployment), the connection gate resolves the
presented token to an account, pins it on `current_account` for the turn, and the run can check the
account's budget before spending and report cost after.

Mirrors infrastructure/llm/model_proxy.py: one process-wide setting, configured once at boot.
The URL comes from env (AGENTD_ACCOUNTS_URL) or config (accounts.api_base); env wins. There is no
secret here — the session token is the client's credential, resolved over the wire.

The account identity is carried by a contextvar so it reaches code deep in the model-call stack
(model_proxy, usage reporting) without threading a parameter through every layer. `_run` sets it
at the top of the turn task; `asyncio.create_task` snapshots the context, so each run is isolated.
"""

from __future__ import annotations

import contextvars
import logging
import os
import time
from pathlib import Path

import httpx

from agent_runtime.config import accounts_api_base
from identity.domain.errors import TokenExpired, TokenInvalid
from identity.infrastructure.jwks_verifier import looks_like_jwt

log = logging.getLogger("agentd.accounts")

_enabled = False
_api_base = ""
_client: httpx.AsyncClient | None = None
#: Local JWT verifier, built at boot from the deployment's discovery document. None => this
#: install has no JWKS to verify against and every token is resolved over HTTP, as before.
_verifier = None
_reported_no_internal_key = False  # one-time log guard for the usage-report no-op path

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


def _configure_verifier(config) -> None:
    """Build the local token verifier from the deployment's discovery document.

    WHAT THIS BUYS: `resolve()` below is called on EVERY socket connect, and today every call is
    an HTTP round trip to the accounts service. With a verifier, a JWT is checked against a cached
    public key — no network, microseconds — and the accounts service stops being something the
    daemon needs in order to answer "who is this?".

    Silently absent when the deployment publishes no `jwks_uri` (every install that predates
    discovery): `configured` is then False and resolve() takes the HTTP path exactly as before.
    """
    global _verifier
    _verifier = None
    try:
        from agent_runtime.infrastructure import platform_discovery
        from identity.infrastructure.jwks_verifier import JwksVerifier

        doc = platform_discovery.resolve(config)
        jwks_uri = str(doc.get("jwks_uri") or "")
        issuer = str(doc.get("issuer") or "")
        if not (jwks_uri and issuer):
            return
        state_dir = getattr(config, "state_dir", None)
        _verifier = JwksVerifier(
            jwks_uri=jwks_uri,
            issuer=issuer,
            # The daemon accepts tokens minted for it. A token scoped only to the proxy must not
            # open a socket, which is what an audience check is for.
            audience="agentd-daemon",
            # Cached to disk so a daemon that boots while the platform is unreachable can still
            # verify the tokens its users already hold.
            cache_path=(Path(state_dir) / "jwks-cache.json") if state_dir else None,
        )
        log.info("accounts: local token verification ENABLED (%s)", jwks_uri)
    except Exception as e:  # noqa: BLE001 — verification is an optimisation; never fail boot
        log.warning("accounts: local token verification unavailable: %s", e)
        _verifier = None


def configure(config) -> None:
    """Read the accounts settings once, at boot. Safe to call again (resets the client + cache).

    ADVERTISED AND ENFORCED ARE DIFFERENT THINGS — see `available()` vs `enabled()` below.
    """
    global _enabled, _api_base, _client
    acc = getattr(config, "accounts", None) or {}
    _api_base = accounts_api_base(config)
    _enabled = bool(_api_base) and (
        os.environ.get("AGENTD_ACCOUNTS_URL") is not None or bool(acc.get("enabled"))
    )
    _resolve_cache.clear()
    _configure_verifier(config)
    # Built whenever a service is CONFIGURED, not only where sign-in is REQUIRED. Resolving a
    # token answers "who is this?", and a laptop needs that answer too — it is what lets one
    # identity mechanism serve both deployments instead of one each.
    _client = httpx.AsyncClient(base_url=_api_base, timeout=5.0) if _api_base else None
    if _enabled:
        log.info("accounts: ENFORCED (%s)", _api_base)
    elif _api_base:
        log.info("accounts: available for sign-in (%s)", _api_base)


def api_base() -> str:
    """The resolved accounts service URL ("" => none configured)."""
    return _api_base


def available() -> bool:
    """CAN anyone sign in — i.e. is there an accounts service to sign in to?

    Not the same question as `enabled()`, and keeping them apart is load-bearing. Merely knowing
    where people log in must never change how existing connections authenticate; when those were
    one flag, configuring sign-in locked the operator out of their own daemon (the connection gate
    stops accepting the machine token the moment accounts are ENFORCED).
    """
    return bool(_api_base)


def enabled() -> bool:
    """Is every connection REQUIRED to present an account token? Hosted deployments only, and
    only on an explicit opt-in (`accounts.enabled` / the AGENTD_ACCOUNTS_URL env var). A URL that
    merely arrived from config or the distribution profile advertises sign-in — it does not
    demand it."""
    return _enabled


def account_id() -> str | None:
    """The current turn's account id, or None (accounts off / unscoped call)."""
    acc = current_account.get()
    return acc.get("account_id") if acc else None


def memory_partition(agent_id: str) -> str:
    """Namespace an agent's long-term memory by the CURRENT account, so two users' notes never mix.
    The memory bank isolates by an ``agent_id`` string column, so folding the account into that key
    (`<account>::<agent>`) partitions memory per-account with NO schema change. Bare ``agent_id``
    when there's no account (desktop/local) — unchanged."""
    acct = account_id()
    base = agent_id or "main"
    return f"{acct}::{base}" if acct else base


def set_account(account: dict | None):
    """Pin the account for the current context; returns the contextvar token to reset with."""
    return current_account.set(account)


def reset_account(token) -> None:
    try:
        current_account.reset(token)
    except (ValueError, LookupError):  # different context (task boundary) — best-effort
        pass


async def resolve(token: str) -> dict | None:
    """Credential -> account dict (or None if unknown/expired). Never raises: a resolve failure
    (service down, bad token) is a None, which the caller treats as unauthorized.

    TWO PATHS. A JWT is verified LOCALLY against cached public keys — no network at all, which is
    what takes the accounts service out of the connect path. A legacy `sess_` token is opaque and
    can only be resolved by asking, so it takes the original HTTP route.

    The locally-verified dict deliberately carries NO `budget_usd`: that number lives in the
    ledger and changes as money is spent, so a value copied into a token minted ten minutes ago
    would be stale by construction. Budget enforcement is the model proxy's pre-call gate, which
    reads it fresh; the daemon's own per-turn check (gateway `_chat`) fetches it when it needs it.
    """
    if not token:
        return None
    now = time.time()
    hit = _resolve_cache.get(token)
    if hit and hit[1] > now:
        return hit[0]

    if _verifier is not None and looks_like_jwt(token):
        try:
            claims = await _verifier.averify(token)
        except TokenExpired:
            # Distinct from invalid: the client should refresh and retry rather than sign in
            # again. The connection gate still refuses, but the log says which it was.
            log.info("connection presented an EXPIRED access token")
            return None
        except TokenInvalid as e:
            log.warning("token verification failed: %s", e)
            return None
        account = {
            "account_id": claims.account_id,
            "email": claims.email,
            "session_token": token,
            "scopes": list(claims.scopes),
            "verified": "local",
            # Carried so the connection can tell an EXPIRED credential from a missing one and
            # ask the client to refresh, instead of failing a turn with a generic error.
            "expires_at": claims.expires_at,
        }
        # Cached only until the token itself expires, so an expiry is never masked by our cache.
        ttl = min(_RESOLVE_TTL, max(1.0, claims.expires_at - now)) if claims.expires_at else _RESOLVE_TTL
        _resolve_cache[token] = (account, now + ttl)
        return account

    if not _api_base or _client is None:
        return None
    try:
        r = await _client.get("/resolve", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        # NAME THE EXCEPTION TYPE. Several httpx errors (the timeouts especially) stringify to
        # "", and this failure signs the user out — so the one log line they have to go on read
        # "accounts resolve failed: " and told them nothing about whether the service was slow,
        # unreachable, or refusing them.
        log.warning("accounts resolve failed (%s): %s", type(e).__name__, e or "no detail")
        return None
    if r.status_code != 200:
        return None
    try:
        acc = r.json()
    except ValueError:
        return None
    # retain the raw token: it is the account's own credential for authenticated reads
    # (e.g. GET /budget) — never persisted, lives only in this process's cache/contextvar.
    acc.setdefault("session_token", token)
    _resolve_cache[token] = (acc, now + _RESOLVE_TTL)
    return acc


def _auth_headers() -> dict:
    """Credential for authenticated accounts-service calls: the internal service key when this
    daemon is trusted infra (AGENTD_ACCOUNTS_INTERNAL_KEY set), else the CURRENT account's own
    session token (the hardened /budget accepts either)."""
    internal = os.environ.get("AGENTD_ACCOUNTS_INTERNAL_KEY", "").strip()
    if internal:
        return {"X-Internal-Key": internal}
    acc = current_account.get()
    token = (acc or {}).get("session_token") or ""
    return {"Authorization": f"Bearer {token}"} if token else {}


async def check_budget(acct_id: str) -> dict | None:
    """Current month-to-date budget view for an account, or None on failure (fail-open: the
    caller decides, but a metering outage must not silently block a paying user)."""
    if not _enabled or not acct_id or _client is None:
        return None
    try:
        r = await _client.get(f"/budget/{acct_id}", headers=_auth_headers())
        if r.status_code == 200:
            return r.json()
    except httpx.HTTPError as e:
        log.warning("accounts budget check failed: %s", e)
    return None


async def report_usage(
    acct_id: str, model: str, in_tokens: int, out_tokens: int, cost_usd: float
) -> dict | None:
    """Append one model call's cost to the account's ledger. Best-effort — a reporting failure
    is logged but never breaks the run.

    Requires AGENTD_ACCOUNTS_INTERNAL_KEY: the ledger is written by TRUSTED infra only. In the
    platform-keys topology the model proxy's own success callback is the single ledger writer
    (it sees every call server-side, tamper-proof), so a daemon without the internal key — every
    desktop, and the default cloud deploy — no-ops here instead of double-counting (or 401ing)
    against the hardened /usage endpoint. The in-memory per-turn accumulator is unaffected."""
    global _reported_no_internal_key
    if not _enabled or not acct_id or _client is None:
        return None
    internal = os.environ.get("AGENTD_ACCOUNTS_INTERNAL_KEY", "").strip()
    if not internal:
        if not _reported_no_internal_key:
            _reported_no_internal_key = True
            log.debug("accounts: no AGENTD_ACCOUNTS_INTERNAL_KEY — usage ledger is proxy-side")
        return None
    try:
        r = await _client.post(
            "/usage",
            headers={"X-Internal-Key": internal},
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
