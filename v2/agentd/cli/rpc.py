"""Synchronous gateway RPC for CLI commands — one request/response over the wire.

CLI commands prefer talking to the LIVE daemon (so installs hot-reload, listings are
live); when none is running they fall back to offline composition. This helper is the
"talk to the live daemon" half: connect (token from the rendezvous file), send one
req frame, wait for the matching res, skipping any event frames that interleave.
"""

from __future__ import annotations

import json
import uuid

from agentd import lifecycle


class DaemonNotRunning(RuntimeError):
    pass


class RpcError(RuntimeError):
    """The daemon answered ok=false; message carries its error."""


def call(method: str, params: dict | None = None, timeout: float = 300.0,
         info: lifecycle.GatewayInfo | None = None) -> dict:
    """One RPC against the running daemon. Raises DaemonNotRunning / RpcError."""
    from websockets.sync.client import connect

    info = info or lifecycle.find_running()
    if info is None:
        raise DaemonNotRunning("no agentd daemon is running (start one with `agentd`)")
    request_id = uuid.uuid4().hex
    with connect(info.connect_url(), open_timeout=10, close_timeout=5) as ws:
        ws.send(json.dumps({"type": "req", "id": request_id, "method": method,
                            "params": params or {}}))
        while True:
            frame = json.loads(ws.recv(timeout=timeout))
            if frame.get("type") == "res" and frame.get("id") == request_id:
                payload = frame.get("payload") or {}
                if not frame.get("ok"):
                    raise RpcError(str(payload.get("error") or "gateway error"))
                return payload
            # event frames (broadcasts to all clients) interleave freely; skip them.
