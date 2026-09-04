"""The CHILD half of the fetch broker: `fetch` that asks the host instead of opening a socket.

Sibling of `child_models.py`, and deliberately built the same way — a plugin author writes the
call they always wrote:

    from agent_runtime.infrastructure.net.outbound import fetch
    res = fetch("https://api.acme.com/v1/x", headers={"Authorization": "Bearer ${ACME_API_KEY}"})

Inside the sandbox that name resolves to the shim below, which sends a `fetch_request` frame and
blocks until the answer comes back. Identical signature, identical return type, so a plugin that
works unsandboxed works sandboxed — which is the only way an isolation mode gets adopted instead
of routed around.

THE STUB IS PRE-SEEDED INTO ``sys.modules``, so the real module is never imported and cannot be
reached around by importing it again. Same reasoning as the model shim, and here it matters more:
the real `outbound.fetch` would substitute `${NAME}` from the CHILD's environment, and the child
must never be the place a credential is resolved.

`Response` is re-exported from the real module rather than redefined. A second dataclass with the
same field names is a thing that drifts, and `res.ok` behaving differently sandboxed would be the
worst possible bug to chase.

ONE READER, NOT TWO. Responses arrive on the same stdin the model bridge reads, dispatched by
frame type — two threads racing on one pipe would interleave partial lines.
"""

from __future__ import annotations

import sys
import threading
import types

#: Hard ceiling on how long the child waits. The host applies its own, shorter per-request
#: timeout; this exists only so a lost response cannot hang the child forever.
RESPONSE_TIMEOUT_S = 900.0

_MODULE = "agent_runtime.infrastructure.net.outbound"


class NetBridge:
    """Sends fetch requests to the host and matches the answers back to their callers."""

    def __init__(self, send) -> None:
        self._send = send  # callable(dict) -> None, writes one frame on the protocol stream
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._next_id = 0

    def request(self, payload: dict) -> dict:
        """Send one request and BLOCK until the host answers. -> the raw response payload."""
        with self._lock:
            self._next_id += 1
            request_id = f"f{self._next_id}"
            slot = {"event": threading.Event(), "response": None}
            self._pending[request_id] = slot
        self._send({"t": "fetch_request", "id": request_id, **payload})
        if not slot["event"].wait(RESPONSE_TIMEOUT_S):
            with self._lock:
                self._pending.pop(request_id, None)
            return {"error": "the sandbox host did not answer the fetch request in time"}
        return slot["response"] or {"error": "the sandbox host returned nothing"}

    def deliver(self, payload: dict) -> bool:
        """Route one frame if it is ours. -> True when it was handled."""
        if payload.get("t") != "fetch_response":
            return False
        with self._lock:
            slot = self._pending.pop(str(payload.get("id") or ""), None)
        if slot is not None:
            slot["response"] = payload
            slot["event"].set()
        return True

    def fail_all(self, reason: str) -> None:
        """Release every waiter — the host is gone and no answer is coming."""
        with self._lock:
            slots, self._pending = list(self._pending.values()), {}
        for slot in slots:
            slot["response"] = {"error": reason}
            slot["event"].set()


def install(bridge: NetBridge) -> None:
    """Pre-seed `sys.modules` with a broker-backed `outbound`. Call BEFORE the plugin is loaded."""
    # Imported for its Response/payload_response only — importing the module here does NOT give
    # the child a socket, and reusing its types is what keeps the two paths identical.
    from agent_runtime.infrastructure.net.outbound import (
        DEFAULT_MAX_BYTES,
        DEFAULT_TIMEOUT_S,
        Response,
        payload_response,
    )

    stub = types.ModuleType(_MODULE)
    stub.__doc__ = "Sandboxed stand-in for outbound: HTTP is performed by the host process."

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
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> Response:
        # timeout_s / max_bytes are accepted and NOT forwarded as authority: the host clamps both
        # from `sandbox_fetch_limits`. Accepting them keeps the signature honest; honouring them
        # would let a plugin raise its own ceiling, which is not a ceiling.
        #
        # file_path crosses as a PATH, never as bytes: the host checks it against the run's
        # readable scope and reads it there. The bytes never transit the JSON pipe — which could
        # not carry them anyway.
        return payload_response(
            bridge.request(
                {
                    "url": url,
                    "method": method,
                    "headers": dict(headers or {}),
                    "params": dict(params or {}) or None,
                    "json": json,
                    "data": data,
                    "file_path": file_path,
                    "file_field": file_field,
                    "form_fields": dict(form_fields or {}) or None,
                }
            )
        )

    stub.fetch = fetch
    stub.Response = Response
    stub.payload_response = payload_response
    stub.DEFAULT_TIMEOUT_S = DEFAULT_TIMEOUT_S
    stub.DEFAULT_MAX_BYTES = DEFAULT_MAX_BYTES
    sys.modules[_MODULE] = stub
