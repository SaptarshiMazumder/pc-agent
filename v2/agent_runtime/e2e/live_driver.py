"""The live drive loop — apply settings, send turns, capture events — over ANY transport.

One loop, several transports; that split is the whole point (the harness README called the Agent
Builder feature "a third transport behind the same analysis", and this is where that lands):

  * `WsGatewayTransport` (here) — a websockets client to a running daemon, used by the CLI
    (`runner.py`) from a source checkout against local or staging.
  * The IN-PROCESS transport (presentation/in_process_gateway_client.py) — used by the `e2e_run`
    plugin tool: same methods, no socket, no auth dance, and therefore identical on desktop and
    hosted. The `?act_as=` trick a socket dial-back leans on is honoured ONLY on a machine-token
    connection with accounts off — it does not authorise on a hosted daemon, which is exactly why
    the tool path must not use a socket.

A transport is duck-typed (DIP — this module names no concrete class beyond its own):
    async call(method, params) -> dict     # request/response; raises when the daemon refuses
    async send(method, params) -> None     # fire a request whose real output is the event stream
    events(session_key) -> stream          # MUST be bound BEFORE the first send(), so no event
                                           # can fall between starting a run and listening to it;
                                           # the stream has `async next(timeout) -> dict | None`
                                           # (None = nothing for `timeout` seconds) and `close()`.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Callable

from .scenario import Scenario
from .trace import Trace, TraceWriter, load_trace

#: How long one turn may go with NO event at all before the run counts as wedged. Generous on
#: purpose: a model download or long render emits nothing while it works.
IDLE_TIMEOUT_S = 900.0


async def drive(
    scenario: Scenario,
    transport: Any,
    out: Path,
    model: str = "",
    idle_timeout: float = IDLE_TIMEOUT_S,
    progress: Callable[[str], None] | None = None,
) -> Trace:
    """Run the scenario over `transport`, stream the trace to `out`, return it loaded.

    The sequence per turn — reference media first (workspace.upload, no model call, exactly what
    the app's "Add reference media" button does), then chat.send with any model-visible
    attachments, then collect events until that run's `agent_end`. Turn N+1 is not sent until
    turn N ended, so trace turn boundaries line up with the scenario's."""
    # ISOLATION: a throwaway session key per run. Nothing here ever touches an existing chat, and
    # the e2e_run tool deletes the session afterwards so test runs don't litter the user's list.
    session_key = f"e2e:{scenario.agent_id}:{uuid.uuid4().hex[:8]}"
    writer = TraceWriter(out)
    writer.meta(scenario=scenario.id, agent_id=scenario.agent_id, model=model,
                session_key=session_key)
    truncated = False

    stream = transport.events(session_key)
    try:
        # Settings first, so the agent runs configured (a backend URL, tokens) — and pin the
        # model under test. Both are account-scoped writes: they never touch anyone else's
        # configuration, and on the tool path they land on the CALLER's own account.
        if scenario.settings:
            await transport.call(
                "config.set", {"agentId": scenario.agent_id, "keys": scenario.settings}
            )
        if model:
            await transport.call(
                "config.set", {"agentId": scenario.agent_id, "patch": {"model": model}}
            )

        for i, turn in enumerate(scenario.turns):
            if i >= scenario.max_turns:
                break
            writer.open_turn(i, turn.text)
            if progress:
                progress(f"turn {i + 1}/{len(scenario.turns)}: {turn.text[:60]}")

            for rel in turn.reference_media:
                fp = _resolve(scenario, rel)
                res = await transport.call("workspace.upload", {
                    "agentId": scenario.agent_id, "path": "references",
                    "name": fp.name,
                    "dataBase64": base64.b64encode(fp.read_bytes()).decode("ascii"),
                })
                if not (res or {}).get("ok", True):
                    raise RuntimeError(
                        f"reference upload failed for {fp.name}: {res.get('error')}"
                    )

            params: dict = {
                "sessionKey": session_key, "agentId": scenario.agent_id,
                "message": turn.text, "traceId": uuid.uuid4().hex,
            }
            atts = []
            for rel in turn.attachments:
                fp = _resolve(scenario, rel)
                mime, _enc = mimetypes.guess_type(fp.name)
                atts.append({
                    "name": fp.name, "mimeType": mime or "application/octet-stream",
                    "dataBase64": base64.b64encode(fp.read_bytes()).decode("ascii"),
                })
            if atts:
                params["attachments"] = atts

            await transport.send("chat.send", params)
            ended = await _collect_turn(stream, session_key, i, writer, idle_timeout, progress)
            if not ended:
                truncated = True
                break
    finally:
        stream.close()

    writer.meta(truncated=truncated)
    writer.close()
    return load_trace(out)


async def _collect_turn(stream, session_key: str, turn: int, writer: TraceWriter,
                        idle_timeout: float, progress: Callable[[str], None] | None) -> bool:
    """Fold events into the trace until this turn's run ends (`agent_end`). Returns False if the
    run wedged (no event for `idle_timeout` seconds) — recorded as truncated, itself a finding.

    `progress` is also a HEARTBEAT: when the driver runs inside another agent's tool call
    (e2e_run), the CALLER's run has its own silence watchdog, and a child quietly rendering for
    twenty minutes must not read as the parent having wedged. Tool starts always report; between
    them, any child activity reports at most once per interval."""
    import time

    last_beat = time.monotonic()
    tool_calls = 0
    while True:
        ev = await stream.next(idle_timeout)
        if ev is None:
            return False
        writer.event(turn, ev)
        et = ev.get("type") or ev.get("event") or ""
        if progress:
            if et == "tool_execution_start":
                tool_calls += 1
                progress(f"turn {turn + 1}: {ev.get('toolName') or ev.get('name') or 'tool'}…")
                last_beat = time.monotonic()
            elif time.monotonic() - last_beat >= 60:
                progress(f"turn {turn + 1}: still running ({tool_calls} tool call(s) so far)")
                last_beat = time.monotonic()
        if et == "agent_end":
            return True


def _resolve(scenario: Scenario, rel: str) -> Path:
    """Media paths are relative to the scenario file, so a scenario and its assets travel
    together in the repo."""
    fp = Path(rel)
    return fp if fp.is_absolute() else scenario.base_dir / rel


# --------------------------------------------------------------------------- ws transport


class WsGatewayTransport:
    """The socket transport — a gateway ws client, `websockets` imported lazily so replay (and
    the in-process tool path) need nothing beyond the stdlib.

    SEQUENTIAL BY CONTRACT: `call` and the event stream share one socket and each reads it
    directly, so they must never run concurrently. `drive` guarantees that — setup calls happen
    before a run starts and uploads happen between runs, while no events are flowing (a stray
    frame between turns would be discarded by `call`, same as the pre-split runner behaved)."""

    def __init__(self, daemon: str, token: str = ""):
        url = daemon.rstrip("/")
        if not url.startswith("ws"):
            url = "ws://" + url.split("://", 1)[-1]
        sep = "&" if "?" in url else "?"
        self._url = f"{url}{sep}session={token}" if token else url
        self._ws = None

    async def __aenter__(self) -> "WsGatewayTransport":
        import websockets  # lazy: only the socket transport needs it

        self._ws = await websockets.connect(self._url, max_size=None, open_timeout=30)
        await self.call("hello", {"protocol": 1})
        return self

    async def __aexit__(self, *exc) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def call(self, method: str, params: dict) -> dict:
        import asyncio

        rid = uuid.uuid4().hex
        await self._ws.send(json.dumps({"type": "req", "id": rid, "method": method,
                                        "params": params}))
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=60)
            frame = json.loads(raw)
            if frame.get("type") == "res" and frame.get("id") == rid:
                if frame.get("ok") is False:
                    raise RuntimeError(str((frame.get("payload") or {}).get("error") or method))
                return frame.get("payload") or {}

    async def send(self, method: str, params: dict) -> None:
        # Fire-and-forget: chat.send's real output is the event stream; its res frame (a runId
        # ack) is skipped by the stream's own filter.
        await self._ws.send(json.dumps({"type": "req", "id": uuid.uuid4().hex,
                                        "method": method, "params": params}))

    def events(self, session_key: str) -> "_WsEventStream":
        return _WsEventStream(self._ws, session_key)


class _WsEventStream:
    def __init__(self, ws, session_key: str):
        self._ws = ws
        self._key = session_key

    async def next(self, timeout: float) -> dict | None:
        import asyncio

        while True:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            if frame.get("type") != "event" or frame.get("event") != "chat.event":
                continue
            payload = frame.get("payload") or {}
            if payload.get("sessionKey") not in (None, self._key):
                continue
            return payload.get("event") or {}

    def close(self) -> None:
        pass  # the transport owns the socket
