"""Platform model-proxy seam — route model calls through OUR LiteLLM proxy.

Default OFF: every model call goes direct to the provider with the local/BYOK key, exactly
as today. When configured (a hosted deployment, or a desktop subscriber), ALL model calls are
retargeted at a LiteLLM proxy that holds OUR provider keys and meters usage per account — the
"platform keys" mode. One process-wide routing setting per daemon, so it lives as a
module seam configured once at boot, mirroring litellm's own global toggles.

The proxy KEY is a secret and comes from the ENVIRONMENT (AGENTD_MODEL_PROXY_KEY), never
config.json — the same discipline as provider keys. On a hosted desktop the key is the user's
accounts SESSION TOKEN, written to ~/.agentd/.env by the `platform.connect` RPC (the proxy's
custom auth resolves it to an account); in the cloud it is the proxy master key. The URL may
come from env (AGENTD_MODEL_PROXY_URL), config (model_proxy.api_base), or the distribution
profile ([platform] model_proxy_url); first one wins in that order.

Enablement: env URL present, or config opted in (model_proxy.enabled), or the URL came from
the distribution profile AND a key is present — a signed-out hosted-flavor desktop has no key,
so the seam stays OFF and BYOK behaves exactly as an open install.

Compatibility: the former AGENTD_MODEL_GATEWAY_* env vars, ``model_gateway`` config object,
and ``model_gateway_url`` distribution field remain read-only fallbacks for existing installs.
New writes always use the Model Proxy names.

apply() rewrites a litellm completion kwargs dict to hit the proxy: model -> litellm_proxy/<model>
(+ api_base + api_key). The proxy's model_list must expose the same model names agentd uses
(a "*" passthrough entry covers them all). Nothing about the tools or the loop changes — the
seam is invisible above it, which is why desktop and hosted run the SAME code.
"""

from __future__ import annotations

import os

_enabled = False
_api_base = ""
_api_key = ""
_source = ""  # "env" | "config" | "distribution" | "" — where the URL came from


def configure(config) -> None:
    """Read the proxy settings, at boot or at runtime (platform.connect re-invokes after
    writing the key to ~/.agentd/.env). Env > config > distribution profile for the URL;
    disabled unless a URL is present and its source opts in. Safe to call again."""
    global _enabled, _api_base, _api_key, _source
    proxy_cfg = getattr(config, "model_proxy", None) or {}
    legacy_cfg = getattr(config, "model_gateway", None) or {}
    mp = proxy_cfg or legacy_cfg
    profile = getattr(config, "distribution", None)
    env_url = (
        os.environ.get("AGENTD_MODEL_PROXY_URL", "").strip()
        or os.environ.get("AGENTD_MODEL_GATEWAY_URL", "").strip()
    )
    cfg_url = str(mp.get("api_base") or "").strip()
    dist_url = str(
        getattr(profile, "model_proxy_url", "")
        or getattr(profile, "model_gateway_url", "")
        or ""
    ).strip()
    url, _source = next(
        ((u, s) for u, s in ((env_url, "env"), (cfg_url, "config"), (dist_url, "distribution")) if u),
        ("", ""),
    )
    _api_base = url.rstrip("/")
    _api_key = (
        os.environ.get("AGENTD_MODEL_PROXY_KEY", "").strip()
        or os.environ.get("AGENTD_MODEL_GATEWAY_KEY", "").strip()
    )
    # on when a URL is given AND its source opts in:
    #   • env url        — explicit hosted/server override, always on (can't be toggled off)
    #   • config enabled — an explicit `model_proxy.enabled = true` forces it on
    #   • config/dist url + KEY — the desktop Local/Cloud switch: a config-override or baked
    #     distribution url activates only once a key exists (Cloud = platform.connect wrote the
    #     session token; Local = platform.disconnect cleared it). Signed-out stays BYOK.
    _enabled = bool(_api_base) and (
        bool(env_url)
        or bool(mp.get("enabled"))
        or (_source in ("config", "distribution") and bool(_api_key))
    )


def enabled() -> bool:
    return _enabled


def status() -> dict:
    """Non-secret view for UIs (Settings 'platform keys vs your own keys' indicator)."""
    return {
        "enabled": _enabled,
        "api_base": _api_base,
        "source": _source,
        "has_key": bool(_api_key),
    }


def apply(kwargs: dict) -> dict:
    """Retarget one litellm completion kwargs dict at the proxy, in place. No-op when the
    proxy is off or the call is already proxied. Overwrites any provider api_key with the
    proxy key (in proxy mode the local provider key must NOT win)."""
    if not _enabled:
        return kwargs
    model = str(kwargs.get("model") or "")
    if model and not model.startswith("litellm_proxy/"):
        kwargs["model"] = f"litellm_proxy/{model}"
    kwargs["api_base"] = _api_base
    if _api_key:
        kwargs["api_key"] = _api_key
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
