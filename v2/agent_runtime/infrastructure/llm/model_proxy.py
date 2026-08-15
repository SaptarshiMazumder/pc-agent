"""Platform model-proxy seam — route model calls through OUR LiteLLM proxy.

Default OFF: every model call goes direct to the provider with the local/BYOK key, exactly
as today. When configured (a hosted deployment, or a desktop subscriber), ALL model calls are
retargeted at a LiteLLM proxy that holds OUR provider keys and meters usage per account — the
"platform keys" mode. One process-wide routing setting per daemon, so it lives as a
module seam configured once at boot, mirroring litellm's own global toggles.

WHICH KEY PAYS depends on the deployment, and the two are decided explicitly rather than by
falling back on each other (see `turn_key`):

  * a SERVER whose operator forced the proxy on pays with AGENTD_MODEL_PROXY_KEY from the
    environment — the master key — and names the account per call via the OpenAI `user` field.
  * everywhere else, the CONNECTION's own session token pays. Nothing is stored: the client
    presents it on connect, and a client that asked for `mode=local` pays with nothing at all.

The URL may come from env (AGENTD_MODEL_PROXY_URL), config (model_proxy.api_base), or the
distribution profile ([platform] model_proxy_url); first one wins in that order.

Compatibility: the former AGENTD_MODEL_GATEWAY_* env vars, ``model_gateway`` config object,
and ``model_gateway_url`` distribution field remain read-only fallbacks for existing installs.
New writes always use the Model Proxy names.

apply() rewrites a litellm completion kwargs dict to hit the proxy: model -> litellm_proxy/<model>
(+ api_base + api_key). The proxy's model_list must expose the same model names agentd uses
(a "*" passthrough entry covers them all). Nothing about the tools or the loop changes — the
seam is invisible above it, which is why desktop and hosted run the SAME code.
"""

from __future__ import annotations

import contextvars
import os

_forced = False  # the DEPLOYMENT routes everything through the proxy (env url / config enabled)
_enabled = False  # alias of _forced, kept for status()
_api_base = ""
_api_key = ""
_source = ""  # "env" | "config" | "distribution" | "" — where the URL came from

LOCAL = "local"
CLOUD = "cloud"

#: WHICH KEYS PAY, per CONNECTION rather than per process.
#:
#: One credential in one file meant one billing answer for the whole machine: a window nobody had
#: signed in on spent whoever had last pressed Cloud somewhere else, and the daemon could not even
#: attribute it, because usage is metered from the turn's account and that connection had none.
#:
#: The credential that pays is now the session on the connection — the same value that says who
#: you are — so paying and being billed cannot disagree, and two windows signed in as different
#: people are billed to different people at the same moment.
#:
#: "" means the client stated no preference, which behaves as CLOUD when it has a session.
current_billing: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentd_billing_mode", default=""
)


def set_billing(mode: str):
    """Pin the billing mode for this connection; returns the token to reset with."""
    mode = (mode or "").strip().lower()
    return current_billing.set(mode if mode in (LOCAL, CLOUD) else "")


def reset_billing(token) -> None:
    try:
        current_billing.reset(token)
    except (ValueError, LookupError):  # different context (task boundary) — best-effort
        pass


def turn_key() -> str:
    """The credential this turn pays with, or "".

    THE CALLER PAYS WITH THE CALLER'S OWN CREDENTIAL whenever this turn has one. The
    deployment's master key covers only the turns that genuinely have no caller behind them:
    cron ticks (which pin an account id but no token), anonymous public-app visitors, and the
    daemon's own system calls.

    WHY THE ORDER MATTERS, AND WHY THIS IS NOT THE CHAIN THIS FUNCTION USED TO FORBID. It read
    "FORCED => master key, otherwise the session", so a hosted server paid for every user with
    one infra credential and named the human in the OpenAI `user` field instead. Billing coped
    (it reads either), but the proxy's PRE-CALL gate identifies the account from the credential
    alone — a master key carries none — so hosted users were metered accurately and could never
    be refused: no credit check, no entitlement check, ever. Paying as the user restores that by
    construction, with no second way to identify an account.

    The hazard the old comment named — "a stale key in a .env decides who got billed" — was a
    chain in the OPPOSITE direction: falling back to an ambient key when no session was present.
    Preferring the caller's own credential cannot bill one person on another's key; the master
    key is reached only where there is no person to bill.
    """
    if current_billing.get() == LOCAL:
        return ""
    from agent_runtime.infrastructure import accounts  # local: accounts imports nothing from llm

    session = str((accounts.current_account.get() or {}).get("session_token") or "")
    if session:
        return session
    return _api_key if _forced else ""


def configure(config) -> None:
    """Read the proxy settings, at boot or at runtime (platform.connect re-invokes after
    writing the key to ~/.agentd/.env). Env > config > distribution profile for the URL;
    disabled unless a URL is present and its source opts in. Safe to call again."""
    global _enabled, _forced, _api_base, _api_key, _source
    proxy_cfg = getattr(config, "model_proxy", None) or {}
    legacy_cfg = getattr(config, "model_gateway", None) or {}
    mp = proxy_cfg or legacy_cfg
    profile = getattr(config, "distribution", None)
    env_url = (
        os.environ.get("AGENTD_MODEL_PROXY_URL", "").strip()
        or os.environ.get("AGENTD_MODEL_GATEWAY_URL", "").strip()
    )
    cfg_url = str(mp.get("api_base") or "").strip()
    # What the DEPLOYMENT says today, ahead of what the installer baked months ago. Same
    # precedence and same reasoning as accounts_api_base — see config.platform_discovered.
    disc_url = ""
    try:
        from agent_runtime.config import platform_discovered

        disc_url = platform_discovered(config, "model_proxy_url")
    except Exception:  # noqa: BLE001 — discovery must never break model routing
        disc_url = ""
    dist_url = str(
        getattr(profile, "model_proxy_url", "")
        or getattr(profile, "model_gateway_url", "")
        or ""
    ).strip()
    url, _source = next(
        (
            (u, s)
            for u, s in (
                (env_url, "env"),
                (cfg_url, "config"),
                (disc_url, "discovery"),
                (dist_url, "distribution"),
            )
            if u
        ),
        ("", ""),
    )
    _api_base = url.rstrip("/")
    _api_key = (
        os.environ.get("AGENTD_MODEL_PROXY_KEY", "").strip()
        or os.environ.get("AGENTD_MODEL_GATEWAY_KEY", "").strip()
    )
    # FORCED ON BY THE DEPLOYMENT — the operator's decision, not the user's. The third case this
    # used to have ("a config/distribution url plus a stored KEY") is gone with the key it
    # depended on: that was the desktop Local/Cloud switch living here as one process-wide flag,
    # so every window was billed however the last one to press Cloud had left it.
    _forced = bool(_api_base) and (bool(env_url) or bool(mp.get("enabled")))
    _enabled = _forced


def enabled() -> bool:
    """Would THIS turn be proxied? Forced by the deployment, or this connection has a session to
    pay with. Per connection — see `current_billing`."""
    return bool(_api_base) and (_forced or bool(turn_key()))


def available() -> bool:
    """Is a proxy configured at all — i.e. is there a Cloud to switch to on this build?"""
    return bool(_api_base)


def status() -> dict:
    """Non-secret view for UIs (Settings 'platform keys vs your own keys' indicator)."""
    return {
        # "would THIS connection's calls be proxied" — what a settings screen is really asking.
        # A process-wide flag showed two windows in different modes the same answer, and one of
        # them was always wrong.
        "enabled": enabled(),
        "api_base": _api_base,
        "source": _source,
        "available": bool(_api_base),
        "has_key": bool(_api_key),
    }


def passthrough(provider: str) -> tuple[str, str]:
    """(base_url, api_key) for a NATIVE provider-SDK call this turn, or ("", "") when direct.

    The same routing decision `apply()` makes for litellm kwargs, for the call shapes litellm
    cannot carry (Gemini image generation returns the picture on generate_content). LiteLLM
    proxies expose native passthrough routes per provider ("<base>/gemini/…" forwards to
    Google with the proxy's own key); the credential that authenticates us to the proxy is the
    turn's own (`turn_key`) — so who pays and who is metered cannot disagree, exactly as with
    chat calls. ("", "") means BYOK: the caller goes direct on the user's own key."""
    if not (provider and enabled()):
        return "", ""
    return f"{_api_base}/{provider.strip().lower()}", turn_key()


def apply(kwargs: dict) -> dict:
    """Retarget one litellm completion kwargs dict at the proxy, in place. No-op when the
    proxy is off or the call is already proxied. Overwrites any provider api_key with the
    proxy key (in proxy mode the local provider key must NOT win)."""
    if not enabled():
        return kwargs
    model = str(kwargs.get("model") or "")
    if model and not model.startswith("litellm_proxy/"):
        kwargs["model"] = f"litellm_proxy/{model}"
    kwargs["api_base"] = _api_base
    # Empty only where the operator forced the proxy on without a key (it authenticates some other
    # way). Writing a blank would overwrite a working provider key with nothing.
    key = turn_key()
    if key:
        kwargs["api_key"] = key
    # per-call attribution for trusted (master-key) callers: the proxy meters session-token
    # callers from the token itself, but the cloud daemon authenticates as infra and names
    # the account explicitly via the OpenAI `user` field.
    from agent_runtime.infrastructure import (
        accounts,  # local import — accounts imports nothing from llm
        telemetry,
    )

    acct = accounts.account_id()
    if acct and "user" not in kwargs:
        kwargs["user"] = acct
    # Carry the tracking number across the process boundary. This is the ONLY point where our
    # correlation context can reach the proxy, and the proxy is where cost is known — so
    # without these headers a usage row can never be tied back to the message that caused it.
    trace = telemetry.trace_headers()
    if trace:
        kwargs["extra_headers"] = {**(kwargs.get("extra_headers") or {}), **trace}
    return kwargs
