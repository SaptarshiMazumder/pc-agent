"""The fetch broker — the HOST side of a sandboxed tool's outbound HTTP call.

Sibling of `model_broker.py`, and the same inversion for the same reason: rather than weaken the
grant so the plugin can dial out, the plugin ASKS and the host performs.

    child   {"t":"fetch_request","id":"f1","url":"https://api.acme.com/v1/x","headers":{...}}
    host    -> check the host against the grant -> substitute ${SECRETS} -> call -> clamp -> reply
    child   {"t":"fetch_response","id":"f1","status":200,"text":"..."}

WHAT THIS BUYS OVER "LET IT HAVE A SOCKET WITH AN ALLOWLIST":

  * The allowlist is ENFORCED, not requested. Blocking `socket.connect` inside the child is an
    interpreter-level control that a compiled extension walks past; refusing to dial is not
    something the caller can argue with, because the caller holds the socket.
  * The CREDENTIAL never crosses. The plugin writes `${ACME_API_KEY}` and the host substitutes —
    so it cannot read the key, cannot keep it, and cannot post it to a host it never declared.
  * It works identically on desktop and hosted, because the side that knows HOW to reach the
    network is the side that does it.

EVERY REFUSAL COMES BACK AS A MESSAGE, never a hang and never a bare failure. A plugin author who
reads "host 'evil.example' is not one this plugin declared" can fix it; one whose call silently
returns nothing cannot.

HONEST LIMIT, stated so nobody over-trusts this: a granted host is still a channel out. A plugin
can encode data into a path on a host it legitimately calls. This bounds WHO can be talked to and
keeps credentials host-side; it is not exfiltration prevention. The declaration's real value is
that a human can read what an agent intends to contact before installing it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from agent_runtime.domain.sandbox import CapabilityGrant
from agent_runtime.domain.sandbox_net import (
    ALLOWED_SCHEMES,
    host_of,
    matches_any,
    scheme_of,
    undeclared_placeholders,
)
from agent_runtime.infrastructure import telemetry
from agent_runtime.infrastructure.net import outbound

log = logging.getLogger("agentd")

#: Ceilings for ONE tool run, overridable via `config.sandbox_fetch_limits`. Generous but finite,
#: on the same reasoning as the model limits: a ceiling that trips on legitimate work gets
#: switched off wholesale, and then it protects nothing.
DEFAULT_FETCH_LIMITS = {
    "max_calls": 32,  # requests per tool invocation
    "max_bytes": 5 * 1024 * 1024,  # per-response body clamp
    "timeout_s": 30.0,  # per-request wall clock
}

#: Methods a plugin may ask for. TRACE is absent deliberately (it reflects headers, including the
#: substituted credential, straight back into a body the plugin then reads).
ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})


class SandboxFetchBroker:
    """Serves the outbound requests of ONE sandboxed tool run. Not shared between runs: the call
    budget is per-invocation, so the counter and the run have the same lifetime."""

    def __init__(
        self,
        config,
        *,
        plugin_id: str,
        tool_name: str,
        grant: CapabilityGrant,
        declared_secrets: tuple[str, ...] = (),
    ) -> None:
        self._config = config
        self._plugin_id = plugin_id
        self._tool_name = tool_name
        self._grant = grant
        self._declared = tuple(declared_secrets or ())
        limits = dict(DEFAULT_FETCH_LIMITS)
        limits.update(dict(getattr(config, "sandbox_fetch_limits", None) or {}))
        self._max_calls = int(limits.get("max_calls") or 0)
        self._max_bytes = int(limits.get("max_bytes") or 0)
        self._timeout_s = float(limits.get("timeout_s") or 0) or DEFAULT_FETCH_LIMITS["timeout_s"]
        self._calls = 0

    @property
    def calls_made(self) -> int:
        return self._calls

    async def serve(self, request: dict) -> dict:
        """One `fetch_request` -> the `fetch_response` to write back. Never raises."""
        request_id = str(request.get("id") or "")
        started = time.monotonic()
        try:
            url, headers = self._authorize(request)
        except _Refused as refusal:
            return self._reply(
                request_id,
                outbound.Response(error=str(refusal), url=str(request.get("url") or "")),
                outcome=refusal.outcome,
            )

        self._calls += 1
        res = await asyncio.to_thread(
            outbound.fetch,
            url,
            method=str(request.get("method") or "GET").upper(),
            headers=headers,
            params=request.get("params") or None,
            json=request.get("json"),
            data=str(request.get("data") or ""),
            timeout_s=self._timeout_s,
            max_bytes=self._max_bytes,
        )
        telemetry.timing(
            "sandbox_fetch_ms",
            int((time.monotonic() - started) * 1000),
            source="sandbox",
            # host, never the URL: a path carries query strings, ids and occasionally a token.
            _props={"plugin_id": self._plugin_id, "tool": self._tool_name, "host": host_of(url)},
        )
        return self._reply(request_id, res, outcome="ok" if not res.error else "error")

    # ------------------------------------------------------------------ policy

    def _authorize(self, request: dict) -> tuple[str, dict]:
        """-> (url, headers-with-secrets-substituted). Raises _Refused with a plugin-visible
        message. Every refusal happens HERE, before the call, so each carries its own outcome tag
        and the metric can say WHY rather than just "failed"."""
        url = str(request.get("url") or "").strip()
        if not url:
            raise _Refused("a fetch request needs a url", outcome="bad-request")
        if not self._grant.net_allowlist:
            raise _Refused(
                "this plugin is not granted network access. Declare the hosts it calls in its "
                "plugin.toml:  [sandbox]  net = [\"api.example.com\"]",
                outcome="not-granted",
            )
        method = str(request.get("method") or "GET").upper()
        if method not in ALLOWED_METHODS:
            raise _Refused(f"method {method!r} is not allowed", outcome="bad-method")
        if self._max_calls and self._calls >= self._max_calls:
            raise _Refused(
                f"request limit reached for this tool run ({self._max_calls}). Raise "
                "`sandbox_fetch_limits.max_calls` if a tool legitimately needs more.",
                outcome="call-cap",
            )
        scheme = scheme_of(url)
        if scheme not in ALLOWED_SCHEMES:
            # file:// would make the broker a file-read oracle — the same hole the model broker's
            # image paths had to close.
            raise _Refused(
                f"scheme {scheme or '(none)'!r} is not allowed; use http or https",
                outcome="bad-scheme",
            )
        host = host_of(url)
        if not matches_any(host, self._grant.net_allowlist):
            raise _Refused(
                f"host '{host}' is not one this plugin may reach "
                f"(allowed: {', '.join(self._grant.net_allowlist)}). The list comes from its "
                "plugin.toml [sandbox] net, narrowed by this daemon's config — not from the "
                "request, or a plugin could name any host at call time.",
                outcome="host-denied",
            )

        raw_headers = {str(k): str(v) for k, v in (request.get("headers") or {}).items()}
        strings = [url, *raw_headers.values(), str(request.get("data") or "")]
        stray = undeclared_placeholders(strings, self._declared)
        if stray:
            raise _Refused(
                f"undeclared secret(s) {', '.join(sorted(stray))} — a plugin may only reference "
                "names it listed in plugin.toml [sandbox] secrets. Asking for an arbitrary one at "
                "call time is how a plugin would read a key it was never given.",
                outcome="secret-denied",
            )
        return url, self._substituted(raw_headers)

    def _substituted(self, headers: dict) -> dict:
        """Fill in the DECLARED `${NAME}`s from the host's environment.

        Resolved here and nowhere else, so the value exists only in this process, only for the
        duration of one request. A name the host cannot resolve is left as the literal `${NAME}`
        by `substitute` — the provider then answers 401 with the placeholder visible, which is a
        debuggable failure, unlike a header that silently went missing.
        """
        from agent_runtime.domain.sandbox_net import substitute

        values = {}
        for name in self._declared:
            value = os.environ.get(name) or self._from_config(name)
            if value:
                values[name] = value
        missing = [n for n in self._declared if n not in values]
        if missing:
            # INFO, not a refusal: the plugin may legitimately not need every declared name on
            # every call, and refusing here would break a tool whose optional key is unset.
            log.info(
                "sandbox: '%s' declares secret(s) %s but this daemon has no value for them",
                self._plugin_id,
                ", ".join(missing),
            )
        return {k: substitute(v, values) for k, v in headers.items()}

    def _from_config(self, name: str) -> str:
        """A declared name may also be answered by the plugin's own settings block, which is
        where an agent's settings page writes a user's key (config.plugins.<id>.secrets.<NAME>)."""
        plugins = getattr(self._config, "plugins", None) or {}
        block = plugins.get(self._plugin_id) if isinstance(plugins, dict) else None
        secrets = (block or {}).get("secrets") if isinstance(block, dict) else None
        value = (secrets or {}).get(name) if isinstance(secrets, dict) else None
        return str(value or "")

    # ------------------------------------------------------------------ replies

    def _reply(self, request_id: str, res: outbound.Response, *, outcome: str) -> dict:
        telemetry.count(
            "sandbox_fetch_request_total",
            source="sandbox",
            _props={"plugin_id": self._plugin_id, "tool": self._tool_name, "outcome": outcome},
        )
        if outcome not in ("ok",):
            log.warning(
                "sandbox: fetch refused for '%s'/'%s' (%s): %s",
                self._plugin_id,
                self._tool_name,
                outcome,
                res.error,
            )
        payload = outbound.response_payload(res)
        payload.update({"t": "fetch_response", "id": request_id})
        return payload


class _Refused(Exception):
    """A request the broker will not serve. The message goes back to the plugin verbatim."""

    def __init__(self, message: str, *, outcome: str) -> None:
        super().__init__(message)
        self.outcome = outcome
