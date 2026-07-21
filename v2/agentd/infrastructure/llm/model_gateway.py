"""Platform model-gateway seam — route model calls through OUR LiteLLM proxy.

Default OFF: every model call goes direct to the provider with the local/BYOK key, exactly
as today. When configured (a hosted deployment, or a desktop subscriber), ALL model calls are
retargeted at a LiteLLM proxy that holds OUR provider keys and meters usage per account — the
"platform keys" mode. One process-wide setting (the daemon has one gateway), so it lives as a
module seam configured once at boot, mirroring litellm's own global toggles.

The gateway KEY is a secret and comes from the ENVIRONMENT (AGENTD_MODEL_GATEWAY_KEY), never
config.json — the same discipline as provider keys. The URL may come from either env
(AGENTD_MODEL_GATEWAY_URL) or config (model_gateway.api_base); env wins.

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


def configure(config) -> None:
    """Read the gateway settings once, at boot. Env overrides config; disabled unless a URL is
    present. Safe to call again (e.g. after a config change + restart)."""
    global _enabled, _api_base, _api_key
    mg = getattr(config, "model_gateway", None) or {}
    url = (os.environ.get("AGENTD_MODEL_GATEWAY_URL") or str(mg.get("api_base") or "")).strip()
    _api_base = url.rstrip("/")
    _api_key = os.environ.get("AGENTD_MODEL_GATEWAY_KEY", "").strip()
    # on when a URL is given AND (env url present OR config opted in)
    _enabled = bool(_api_base) and (
        os.environ.get("AGENTD_MODEL_GATEWAY_URL") is not None or bool(mg.get("enabled"))
    )


def enabled() -> bool:
    return _enabled


def apply(kwargs: dict) -> dict:
    """Retarget one litellm completion kwargs dict at the proxy, in place. No-op when the
    gateway is off or the call is already proxied. Overwrites any provider api_key with the
    gateway key (in gateway mode the local provider key must NOT win)."""
    if not _enabled:
        return kwargs
    model = str(kwargs.get("model") or "")
    if model and not model.startswith("litellm_proxy/"):
        kwargs["model"] = f"litellm_proxy/{model}"
    kwargs["api_base"] = _api_base
    if _api_key:
        kwargs["api_key"] = _api_key
    return kwargs
