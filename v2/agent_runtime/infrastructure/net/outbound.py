"""`fetch` — the ONE outbound HTTP call plugin code makes, sandboxed or not.

A plugin writes this and never writes anything else:

    from agent_runtime.infrastructure.net.outbound import fetch

    res = fetch(
        "https://api.acme.com/v1/things",
        headers={"Authorization": "Bearer ${ACME_API_KEY}"},
    )
    if res.ok:
        data = res.json()

Unsandboxed (the author's own machine) that runs here and reads `${ACME_API_KEY}` from the
process environment. Sandboxed, the name resolves to a SHIM (`sandbox/child_net.py`) that asks
the host to do it — same signature, same return type, so the plugin has one code path. That is
the same trick the model broker plays with `oneshot.text_complete`, for the same reason: an
isolation mode that makes authors write different code is one they route around.

WHY `${NAME}` INSTEAD OF A KEY ARGUMENT. The value must never reach a sandboxed plugin — it would
own the credential the moment it could read it, and could then post it anywhere it was allowed to
call. A placeholder lets the plugin SAY where the secret belongs without ever holding it: the host
substitutes at the last moment, only for names the plugin declared in `[sandbox] secrets`. On the
author's own machine the substitution happens here from `os.environ`, so the two paths agree.

`Response` is deliberately small and JSON-shaped: it has to cross a pipe. A streaming body or a
raw socket has no representation there, so neither is offered rather than half-offered.
"""

from __future__ import annotations

import json as _json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime.domain.sandbox_net import substitute
from agent_runtime.infrastructure.files import guess_mime

#: Kept modest on purpose. A plugin pulling a 500MB body into a subprocess and back through a pipe
#: is a memory incident, not a feature; the host clamps this too (`sandbox_fetch_limits`).
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_S = 30.0

#: What an error body is allowed to cost. It is read only to be shown, never parsed.
_ERROR_BODY_BYTES = 2000

#: Content types read as TEXT. Anything else is a file: it is downloaded with `save_path` or not
#: at all. Decoding a model checkpoint as a web page is how a research call once pulled gigabytes
#: into the daemon and froze it.
_TEXT_TYPES = ("text/", "application/json", "application/xml", "application/javascript",
               "application/x-ndjson", "application/x-www-form-urlencoded", "application/yaml",
               "application/x-yaml", "application/toml")


def _is_text(content_type: str, head: bytes) -> bool:
    """True when a body is meant to be read as text. The declared type decides; with none
    declared, the first bytes do: a NUL byte never appears in text."""
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype:
        return ctype.startswith(_TEXT_TYPES) or ctype.endswith(("+json", "+xml"))
    return b"\x00" not in head[:1024]


def _read_bounded(r, max_bytes: int, deadline: float) -> tuple[bytes, bool]:
    """At most `max_bytes` of a streamed body, and whether more was left unread.

    THE BOUND IS ON WHAT IS HELD, NOT ON WHAT IS KEPT. The old path read the whole body and
    clamped afterwards, so a 5 MB limit still buffered a multi-GB response first. Here reading
    STOPS at the limit, and `deadline` bounds the wall clock too: httpx's timeout is per
    operation, which a steady stream never trips.
    """
    buf = bytearray()
    for chunk in r.iter_bytes():
        buf += chunk[: max_bytes - len(buf)] if max_bytes else chunk
        if max_bytes and len(buf) >= max_bytes:
            return bytes(buf), True
        if time.monotonic() > deadline:
            raise TimeoutError("the response did not finish within the time limit")
    return bytes(buf), False


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


@dataclass
class Response:
    """One HTTP response, in the only shape that survives a process boundary."""

    status: int = 0
    headers: dict = field(default_factory=dict)
    text: str = ""
    url: str = ""
    error: str = ""  # non-empty => the request did not complete; `status` is meaningless

    @property
    def ok(self) -> bool:
        return not self.error and 200 <= self.status < 300

    def json(self):
        """Parsed body. Raises ValueError on non-JSON — a tool that assumed JSON should hear
        about it rather than get a None it will dereference two lines later."""
        return _json.loads(self.text)


def _resolved(value: str) -> str:
    """Substitute `${NAME}` from this process's environment (the UNSANDBOXED path).

    A name the RUNNING AGENT declared under `agent.toml [[settings]]` is stored privately, as
    `<agent-id>__NAME`, so two agents can hold two different accounts for the same service.
    Everything else is the machine-wide variable it has always been. `current_setting_env` owns
    that rule; this function only has to ask.
    """
    from agent_runtime.application.run_context import current_oauth_token, current_setting_value

    re = __import__("re")
    # `${oauth:<name>}` — a LIVE token from the connection the agent signed in to, refreshed on
    # the way out if it was about to expire. Substituted here for the same reason a secret is:
    # the plugin says where the credential belongs and never holds one.
    resolved = re.sub(
        r"\$\{oauth:([A-Za-z0-9_-]+)\}",
        lambda m: current_oauth_token(m.group(1)) or m.group(0),
        value or "",
    )
    names = {}
    for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", resolved):
        # PER CALLER, not per process. `current_setting_value` layers the account's stored
        # value over the author's default, and falls back to the machine-wide variable only
        # for a name this agent never declared. Reading `os.environ` here instead is what let
        # one tenant's key answer for every tenant.
        got = current_setting_value(name)
        if got:
            names[name] = got
    return substitute(resolved, names)


def _in_workspace(path: str) -> Path:
    """A relative file path means WORKSPACE-relative — the same convention the sandbox broker
    applies on the host, so a plugin can name `uploads/photo.png` and mean the same file on
    both paths. Without this the unsandboxed fetch read and wrote against the process CWD,
    which on a daemon is nowhere near the run; the sandboxed one was right and the in-process
    one silently was not. An absolute path is left alone."""
    from agent_runtime.application.run_context import current_workspace

    p = Path(path)
    if p.is_absolute():
        return p
    return Path(current_workspace(".") or ".") / p


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    params: dict | None = None,
    json=None,
    data: str = "",
    file_path: str = "",
    file_field: str = "file",
    form_fields: dict | None = None,
    save_path: str = "",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Response:
    """Perform one HTTP request. Never raises — a failure comes back as `Response.error`.

    Never raising is not defensiveness: this is called from inside a tool's `execute`, where an
    exception becomes a crashed tool call instead of an answer the model can act on. The caller
    checks `.ok` and reports; that is the contract every tool in this codebase already follows.

    ``file_path`` UPLOADS A LOCAL FILE as multipart/form-data — the shape browser file inputs
    and endpoints like ComfyUI's ``/upload/image`` expect, which a string body cannot carry
    (image bytes are not text, and the sandbox pipe is JSON frames). The FILE IS READ HERE, on
    the side that has the filesystem: a sandboxed plugin sends only the path, and the broker
    checks it against the run's readable scope before this runs. ``file_field`` names the form
    part; ``form_fields`` are the plain text parts riding alongside (e.g. a subfolder).

    ``save_path`` DOWNLOADS the response body to a local file, bytes untouched — the other
    direction of the same asymmetry: a rendered image or video is not text, `Response.text`
    would mangle it, and the sandbox pipe could not carry it anyway. The bytes are STREAMED to
    disk on this side; the plugin gets back a Response whose ``text`` names the saved file and
    size. A response bigger than ``max_bytes`` is refused whole, never truncated — a clipped
    PNG is a corrupt PNG, which is worse than an honest error. On a non-2xx status nothing is
    written (an error page saved as `render.png` would LOOK downloaded).
    """
    import httpx

    try:
        # MERGE params into any query already on the URL — httpx REPLACES the query when
        # `params` is given, which silently strips credentials the broker folded in from a
        # user-hosted URL (`?token=…` from a vast/RunPod paste). A dropped token reads as a
        # baffling 401 on exactly one tool, so the merge happens here, for every caller.
        if params:
            from urllib.parse import urlencode, urlsplit, urlunsplit

            p = urlsplit(url)
            merged = "&".join(q for q in (p.query, urlencode(params)) if q)
            url = urlunsplit((p.scheme, p.netloc, p.path, merged, p.fragment))
            params = None
        req_headers = {k: _resolved(str(v)) for k, v in (headers or {}).items()}
        deadline = time.monotonic() + timeout_s
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            if save_path:
                return _download(client, method, _resolved(url), req_headers, save_path, max_bytes)
            # AN UPLOAD STREAMS FROM DISK: httpx reads a file object in chunks for multipart,
            # where `read_bytes()` held the whole file in memory for the length of the call.
            upload = open(_in_workspace(file_path), "rb") if file_path else None
            try:
                files = (
                    {file_field: (Path(file_path).name, upload, guess_mime(Path(file_path)))}
                    if upload
                    else None
                )
                with client.stream(
                    method.upper(),
                    _resolved(url),
                    headers=req_headers,
                    json=json,
                    # httpx builds the multipart body from files= + data=; the string body
                    # below is only used when no file rides along.
                    files=files,
                    data={str(k): str(v) for k, v in form_fields.items()} if files and form_fields else None,
                    content=_resolved(data) if data and not files else None,
                ) as r:
                    return _text_response(r, max_bytes, deadline)
            finally:
                if upload:
                    upload.close()
    except Exception as e:  # noqa: BLE001 — a transport failure is the tool's error, not a crash
        return Response(error=f"{type(e).__name__}: {e}", url=url)


def _text_response(r, max_bytes: int, deadline: float) -> Response:
    """A streamed response read as text, never holding more than `max_bytes` of it.

    A BODY THAT IS A FILE IS REFUSED, NOT READ. Its declared type is checked before a byte is
    taken: a checkpoint, a video or an archive comes back as an error saying what it is and how
    big, which a caller can act on (fetch it with `save_path`, or not at all), instead of
    megabytes of decoded noise. A long TEXT body keeps its first `max_bytes`, as it always did;
    only the memory that cost is new.
    """
    headers = {k.lower(): v for k, v in r.headers.items()}
    # A HEAD has no body to refuse: its headers ARE the answer, and asking a file's type and
    # size without downloading it is exactly what HEAD is for (the installer's link check).
    if r.request.method == "HEAD":
        return Response(status=r.status_code, headers=headers, url=str(r.url))
    ctype = headers.get("content-type", "")
    length = str(headers.get("content-length") or "")
    size = f", {_mb(int(length))}" if length.isdigit() else ""
    if ctype and not _is_text(ctype, b""):
        return Response(
            status=r.status_code,
            headers=headers,
            error=f"the response is a file ({ctype.split(';')[0]}{size}), not text; nothing was read. "
            "Download it with save_path, or do not fetch it.",
            url=str(r.url),
        )
    body, _ = _read_bounded(r, max_bytes, deadline)
    if not _is_text(ctype, body):
        return Response(
            status=r.status_code,
            headers=headers,
            error=f"the response is binary{size}, not text; nothing was kept. "
            "Download it with save_path, or do not fetch it.",
            url=str(r.url),
        )
    return Response(
        status=r.status_code,
        headers=headers,
        text=body.decode(r.encoding or "utf-8", "replace"),
        url=str(r.url),
    )


def _download(client, method: str, url: str, headers: dict, save_path: str, max_bytes: int) -> Response:
    """`save_path`: the body streamed to a workspace file, bytes untouched, refused whole past
    `max_bytes` (a clipped PNG is a corrupt PNG). A non-2xx status writes nothing, since an
    error page saved as `render.png` would LOOK downloaded, and only its first bytes are read."""
    dest = _in_workspace(save_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with client.stream(method.upper(), url, headers=headers) as r:
        if r.status_code >= 300:
            head, _ = _read_bounded(r, _ERROR_BODY_BYTES, float("inf"))
            return Response(
                status=r.status_code,
                headers={k.lower(): v for k, v in r.headers.items()},
                text=head.decode(r.encoding or "utf-8", "replace"),
                url=str(r.url),
            )
        written = 0
        with open(dest, "wb") as out:
            for chunk in r.iter_bytes():
                written += len(chunk)
                if max_bytes and written > max_bytes:
                    out.close()
                    dest.unlink(missing_ok=True)
                    return Response(
                        status=r.status_code,
                        error=f"response exceeds max_bytes ({max_bytes}); nothing saved",
                        url=str(r.url),
                    )
                out.write(chunk)
        return Response(
            status=r.status_code,
            headers={k.lower(): v for k, v in r.headers.items()},
            text=f"saved {written} bytes to {dest}",
            url=str(r.url),
        )


def response_payload(res: Response) -> dict:
    """Response -> a JSON-safe dict for the wire."""
    return {
        "status": res.status,
        "headers": res.headers,
        "text": res.text,
        "url": res.url,
        "error": res.error,
    }


def payload_response(payload: dict) -> Response:
    """The inverse, tolerant — a malformed frame must surface as a failed request."""
    payload = payload if isinstance(payload, dict) else {}
    return Response(
        status=int(payload.get("status") or 0),
        headers=dict(payload.get("headers") or {}),
        text=str(payload.get("text") or ""),
        url=str(payload.get("url") or ""),
        error=str(payload.get("error") or ""),
    )
