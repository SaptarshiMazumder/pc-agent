"""The fetch broker — the HOST side of a sandboxed tool's outbound HTTP call.

Sibling of `model_broker.py`, and the same inversion for the same reason: rather than weaken the
grant so the plugin can dial out, the plugin ASKS and the host performs.

    child   {"t":"fetch_request","id":"f1","url":"https://api.acme.com/v1/x","headers":{...}}
    host    -> resolve ${SETTINGS} -> operator deny/allow -> substitute ${SECRETS} -> call -> reply
    child   {"t":"fetch_response","id":"f1","status":200,"text":"..."}

THE NETWORK IS OPEN (2026-09). Plugins fetch any host; the per-plugin reach allowlist is gone.
It made the platform's most-wanted tool shape — "research anything on the internet" — impossible
to write as a plugin, and the threat it guarded against is handled organisationally instead:
plugins only arrive by explicit share today, and marketplace distribution gets a review step.
`[sandbox] net` still exists with ONE job: naming which ${SETTING}s may appear inside a URL, so
`${COMFYUI_URL}/api/x` resolves per-account (that mechanism is untouched).

WHAT THE BROKER STILL BUYS:

  * The CREDENTIAL never crosses into plugin code. The plugin writes `${ACME_API_KEY}` and the
    host substitutes at send time — the value exists only in this process, per request.
  * A plugin can only NAME secrets it declared — asking for an arbitrary name at call time is
    still refused, so it cannot read a key it was never given.
  * The OPERATOR's deny/allow knobs still bind — a hosted deployment can fence off its own
    metadata endpoints and internal ports. Deployment-level, default empty.
  * It works identically on desktop and hosted, because the side that knows HOW to reach the
    network is the side that does it.

EVERY REFUSAL COMES BACK AS A MESSAGE, never a hang and never a bare failure.

HONEST LIMIT, stated so nobody over-trusts this: with open reach, a plugin that holds data can
send it anywhere, and a substituted secret travels to whatever URL the plugin wrote. What remains
is credential HANDLING hygiene (values never enter plugin code), not exfiltration prevention —
that is the review step's job.
"""

from __future__ import annotations

import asyncio
import logging
import time

from agent_runtime.domain.sandbox import CapabilityGrant
from agent_runtime.domain.sandbox_net import (
    ALLOWED_SCHEMES,
    PLACEHOLDER,
    deny_matches,
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

        # A FILE RIDING ALONG (multipart upload). The path is checked HERE, not in `fetch`:
        # the fs sandbox still stands — a child that could name any path would turn the broker
        # into a disk-read oracle posting /etc/passwd to a server of its choice. The run's own
        # readable scope (workspace + granted read paths) is exactly what it may send.
        file_path = str(request.get("file_path") or "")
        if file_path:
            try:
                file_path = str(self._readable(file_path))
            except _Refused as refusal:
                return self._reply(
                    request_id,
                    outbound.Response(error=str(refusal), url=url),
                    outcome=refusal.outcome,
                )

        # A DOWNLOAD DESTINATION — the write-side twin of the upload check above, and stricter:
        # only the run's own writable roots (its workspace), never the agent's definition dirs,
        # which are readable-shipped-data and must stay exactly what the author shipped.
        save_path = str(request.get("save_path") or "")
        if save_path:
            try:
                save_path = str(self._writable(save_path))
            except _Refused as refusal:
                return self._reply(
                    request_id,
                    outbound.Response(error=str(refusal), url=url),
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
            file_path=file_path,
            file_field=str(request.get("file_field") or "file"),
            form_fields=request.get("form_fields") or None,
            save_path=save_path,
            timeout_s=self._timeout_s,
            # A media download legitimately dwarfs a text response; the text clamp would refuse
            # every video. 200MB is a generous ceiling for a rendered output, not a policy knob.
            max_bytes=max(self._max_bytes, 200 * 1024 * 1024) if save_path else self._max_bytes,
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

    def _readable(self, path: str):
        """The resolved path IF this run may read it; raises _Refused otherwise.

        Scope is the grant's own fs view: the run's workspace (`fs_paths`) plus the agent's
        shipped files (`read_paths`). A relative path means workspace-relative — the same
        convention as every file tool, so `uploads/photo.png` names the thing a chat
        attachment just became.
        """
        from pathlib import Path

        from agent_runtime.application.write_scope import is_inside

        roots = [r for r in (*(self._grant.fs_paths or ()), *(self._grant.read_paths or ())) if r]
        p = Path(path)
        if not p.is_absolute() and roots:
            p = Path(roots[0]) / p
        try:
            resolved = p.resolve()
        except OSError as e:
            raise _Refused(f"cannot resolve {path!r}: {e}", outcome="file-denied") from e
        if not any(is_inside(resolved, r) for r in roots):
            raise _Refused(
                f"'{path}' is outside this run's files (workspace and the agent's own "
                "directory). A plugin uploads the run's files, not the machine's.",
                outcome="file-denied",
            )
        if not resolved.is_file():
            raise _Refused(f"no such file to upload: '{path}'", outcome="file-denied")
        return resolved

    def _writable(self, path: str):
        """The resolved path IF this run may write it; raises _Refused otherwise.

        WRITE scope is `fs_paths` alone — `read_paths` (the agent's shipped files) is
        deliberately absent: a download that could land inside the definition would let a
        remote server rewrite the agent's own code. Relative means workspace-relative, same as
        `_readable`.
        """
        from pathlib import Path

        from agent_runtime.application.write_scope import is_inside

        roots = [r for r in (self._grant.fs_paths or ()) if r]
        p = Path(path)
        if not p.is_absolute() and roots:
            p = Path(roots[0]) / p
        try:
            resolved = p.resolve()
        except OSError as e:
            raise _Refused(f"cannot resolve {path!r}: {e}", outcome="file-denied") from e
        if not any(is_inside(resolved, r) for r in roots):
            raise _Refused(
                f"'{path}' is outside this run's writable space (its workspace). A download "
                "lands in the run's own files, nowhere else.",
                outcome="file-denied",
            )
        return resolved

    def _host_settings(self) -> tuple[str, ...]:
        """Setting names the plugin declared AS HOSTS in `[sandbox] net`.

        They are legal in a URL for the same reason a declared secret is legal in a header: the
        plugin named them in its manifest, where a human read them before installing. Without
        this they would trip the undeclared-placeholder guard, which exists to stop a plugin
        asking for a name it never disclosed — not to stop it using one it did.
        """
        return tuple(
            m.group(1)
            for entry in (self._grant.net_allowlist or ())
            for m in [PLACEHOLDER.fullmatch(str(entry).strip())]
            if m
        )

    def _resolve_value(self, name: str) -> str:
        """One name's value for the CALLER — the same resolver the unsandboxed path uses.

        `current_setting_value` layers the account's stored value over the author's default and
        the agent's own prefixed variable. Reading `os.environ` directly here (as this did) meant
        a value stored per account was invisible to a sandboxed plugin while working perfectly
        in-process — the exact "works for you, 401s for them" split the two paths must not have.
        """
        from agent_runtime.application.run_context import current_setting_value

        return current_setting_value(name) or self._from_config(name) or ""

    def _resolve_url(self, url: str) -> str:
        """Substitute the settings a plugin may use in a URL, BEFORE anything is checked.

        Order matters: scheme and host have to be judged on the address that will actually be
        dialled. Validating the raw `${COMFYUI_URL}/api/x` instead reads its scheme as "(none)"
        and refuses a request that was always going to be legitimate.
        """
        from agent_runtime.domain.sandbox_net import substitute

        allowed = set(self._host_settings()) | set(self._declared)
        values = {n: v for n in allowed if (v := self._resolve_value(n))}
        return substitute(url, values)

    def _authorize(self, request: dict) -> tuple[str, dict]:
        """-> (url, headers-with-secrets-substituted). Raises _Refused with a plugin-visible
        message. Every refusal happens HERE, before the call, so each carries its own outcome tag
        and the metric can say WHY rather than just "failed"."""
        url = str(request.get("url") or "").strip()
        if not url:
            raise _Refused("a fetch request needs a url", outcome="bad-request")
        # NO REACH GATE. Plugins fetch any host — `[sandbox] net` no longer means "the hosts you
        # may call"; its one remaining job is naming which ${SETTING}s are legal inside a URL.
        # The gate was lifted deliberately (2026-09): it made "research anything on the internet"
        # impossible to build as a plugin tool, and distribution is share-only for now, with a
        # marketplace review step planned for weeding out malicious plugins instead.
        # RESOLVED FIRST. Everything below judges the address the host will really dial.
        url = self._resolve_url(url)
        # A name that survived substitution is one the user has not filled in. Say THAT, rather
        # than letting it fall through to "scheme '(none)' is not allowed" — the setting is the
        # thing they can fix, and the scheme error points at the plugin instead of at them.
        for m in PLACEHOLDER.finditer(url):
            if m.group(1) in set(self._host_settings()) | set(self._declared):
                raise _Refused(
                    f"setting {m.group(1)} is empty, so this request has no address to go to. "
                    "Fill it in on this agent's settings and try again.",
                    outcome="setting-unset",
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
        # THE OPERATOR'S KNOBS SURVIVE THE OPEN NETWORK. They are the deployment protecting its
        # own machine (a hosted daemon denying 169.254.169.254 and localhost admin ports), not a
        # plugin limitation — default empty, so on a desktop this is two no-ops.
        host = host_of(url)
        deny = getattr(self._config, "sandbox_net_deny", None) or ()
        strict = tuple(
            p for p in (getattr(self._config, "sandbox_net_allow", None) or ()) if str(p).strip()
        )
        if deny_matches(host, deny) or (strict and not matches_any(host, strict)):
            raise _Refused(
                f"host '{host}' is blocked by this daemon's operator config "
                "(sandbox_net_deny / sandbox_net_allow) — a deployment-level rule, not the "
                "plugin's declaration.",
                outcome="host-denied",
            )

        raw_headers = {str(k): str(v) for k, v in (request.get("headers") or {}).items()}
        strings = [url, *raw_headers.values(), str(request.get("data") or "")]
        stray = undeclared_placeholders(strings, tuple(self._declared) + self._host_settings())
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
            # Same resolution as the unsandboxed path (`net.outbound._resolved`), through the
            # one resolver: the caller's stored value, then the author's default, then this
            # agent's own prefixed variable. The two paths MUST agree — a plugin that works
            # in-process and 401s sandboxed gets blamed on the sandbox.
            value = self._resolve_value(name)
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
