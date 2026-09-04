"""The runner — drive a scenario against a real daemon, capture the trace, print the diagnosis.

Two transports, one analysis:
  * LIVE (`--daemon <ws-url> --token <session>`) — connects to a running daemon as a client,
    applies the scenario's settings, sends each user turn, and records every `chat.event` until
    the turn's run ends. This is the true end-to-end path: a real model, the real agent, the
    real ComfyUI the settings point at.
  * REPLAY (`--replay <trace.jsonl>`) — skips the daemon and re-analyses a saved trace. This is
    how the diagnosis is developed and regression-tested offline, without paying for a live run
    every time — and how CI can assert the signal engine without a model.

Both end in the same place: `signals.diagnose` + `checks.run_checks` + `report.render`. The
transport is the only thing that differs, which is the groundwork point — the Agent Builder
feature is a third transport (its own daemon) behind the same analysis.

Transport note: LIVE speaks the gateway's frame protocol (presentation/protocol.py) — a `req`
to `chat.send`, then `event` frames whose `chat.event` payload carries one `AgentEvent` each,
collected until `agent_end`. `websockets` is the only extra dependency and is imported lazily so
REPLAY needs nothing beyond the stdlib.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import sys
import uuid
from pathlib import Path

from . import checks as checks_mod
from . import report as report_mod
from . import signals
from .scenario import Scenario
from .trace import Trace, TraceWriter, load_trace


# --------------------------------------------------------------------------- LIVE transport


async def _drive_live(scenario: Scenario, daemon: str, token: str, out: Path, model: str) -> Trace:
    import websockets  # lazy: replay must not require it

    url = daemon.rstrip("/")
    if not url.startswith("ws"):
        url = "ws://" + url.split("://", 1)[-1]
    sep = "&" if "?" in url else "?"
    conn_url = f"{url}{sep}session={token}" if token else url

    writer = TraceWriter(out)
    writer.meta(scenario=scenario.id, agent_id=scenario.agent_id, model=model)
    session_key = f"e2e:{scenario.agent_id}:{uuid.uuid4().hex[:8]}"
    truncated = False

    async with websockets.connect(conn_url, max_size=None, open_timeout=30) as ws:
        await _req(ws, "hello", {"protocol": 1})

        # Apply settings first, so the agent runs configured (a ComfyUI URL, tokens) — and pin
        # the model under test. Both are scoped to this connection's account, so a run never
        # touches anyone else's configuration.
        if scenario.settings:
            await _req(ws, "config.set", {"agentId": scenario.agent_id, "keys": scenario.settings})
        if model:
            await _req(ws, "config.set", {"agentId": scenario.agent_id, "patch": {"model": model}})

        for i, turn in enumerate(scenario.turns):
            if i >= scenario.max_turns:
                break
            writer.open_turn(i, turn.text)
            run_id = str(uuid.uuid4().hex)
            params = {
                "sessionKey": session_key, "agentId": scenario.agent_id,
                "message": turn.text, "traceId": run_id,
            }
            # A turn's attachments travel the same way a real client sends them: base64 in
            # chat.send, saved by the gateway into the agent's uploads/. Paths resolve against
            # the scenario file so a scenario and its reference images travel together.
            atts = []
            for rel in turn.attachments:
                fp = Path(rel)
                if not fp.is_absolute():
                    fp = scenario.base_dir / rel
                data = base64.b64encode(fp.read_bytes()).decode("ascii")
                mime, _enc = mimetypes.guess_type(fp.name)
                atts.append({"name": fp.name, "mimeType": mime or "application/octet-stream",
                             "dataBase64": data})
            if atts:
                params["attachments"] = atts
            await _send(ws, "chat.send", params)
            ended = await _collect_turn(ws, session_key, i, writer)
            if not ended:
                truncated = True
                break

    writer.meta(truncated=truncated)
    writer.close()
    return load_trace(out)


async def _collect_turn(ws, session_key: str, turn: int, writer: TraceWriter,
                        idle_timeout: float = 900.0) -> bool:
    """Read frames until this turn's run ends (`agent_end`). Returns False if it wedged (no event
    for `idle_timeout` seconds) — which the trace records as truncated, itself a finding."""
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=idle_timeout)
        except asyncio.TimeoutError:
            return False
        try:
            frame = json.loads(raw)
        except ValueError:
            continue
        if frame.get("type") != "event" or frame.get("event") != "chat.event":
            continue
        payload = frame.get("payload") or {}
        if payload.get("sessionKey") not in (None, session_key):
            continue
        ev = payload.get("event") or {}
        writer.event(turn, ev)
        if (ev.get("type") or ev.get("event")) == "agent_end":
            return True


async def _req(ws, method: str, params: dict) -> dict:
    """Send a request and wait for its matching response (used for hello/config.set)."""
    rid = uuid.uuid4().hex
    await ws.send(json.dumps({"type": "req", "id": rid, "method": method, "params": params}))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=60)
        frame = json.loads(raw)
        if frame.get("type") == "res" and frame.get("id") == rid:
            return frame.get("payload") or {}


async def _send(ws, method: str, params: dict) -> None:
    """Fire a request without blocking on its response — chat.send's real output is the event
    stream, which `_collect_turn` reads; its `res` frame (a runId ack) is ignored there."""
    await ws.send(json.dumps({"type": "req", "id": uuid.uuid4().hex, "method": method, "params": params}))


# --------------------------------------------------------------------------- analysis (shared)


def analyse(trace: Trace, scenario: Scenario | None) -> str:
    findings = signals.diagnose(trace)
    results = checks_mod.run_checks(trace, scenario.checks) if scenario else []
    return report_mod.render(trace, findings, results, goal=scenario.goal if scenario else "")


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    # The report uses a few non-ASCII glyphs; a legacy console codepage (cp932/cp1252) would
    # crash the print. Force UTF-8 on our own streams where the platform allows it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Run or re-analyse an agent e2e scenario.")
    ap.add_argument("--scenario", help="scenario JSON (required for a live run; optional for replay)")
    ap.add_argument("--daemon", help="daemon ws URL for a LIVE run, e.g. wss://staging.example")
    ap.add_argument("--token", default="", help="account session token for the live daemon")
    ap.add_argument("--model", default="", help="model to pin for the live run (config.set, account-scoped); also the report label")
    ap.add_argument("--replay", help="analyse a saved trace.jsonl instead of running")
    ap.add_argument("--out", default="", help="where to write the live trace (default beside scenario)")
    ap.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                    help="override a scenario setting for this run (repeatable). Live credentials "
                         "ride here so the committed scenario never holds them.")
    args = ap.parse_args(argv)

    scenario = Scenario.load(args.scenario) if args.scenario else None
    for kv in args.overrides:
        if "=" not in kv:
            ap.error(f"--set expects KEY=VALUE, got {kv!r}")
        if not scenario:
            ap.error("--set needs --scenario")
        k, v = kv.split("=", 1)
        scenario.settings[k] = v

    if args.replay:
        trace = load_trace(args.replay)
        if scenario:
            trace.scenario = trace.scenario or scenario.id
        print(analyse(trace, scenario))
        return 0

    if not (args.daemon and scenario):
        ap.error("a live run needs --daemon and --scenario (or use --replay)")

    out = Path(args.out) if args.out else Path(args.scenario).with_suffix(".trace.jsonl")
    trace = asyncio.run(_drive_live(scenario, args.daemon, args.token, out, args.model))
    print(analyse(trace, scenario))
    print(f"\ntrace saved: {out}")
    # Non-zero exit if any check failed — so CI / a wrapper can gate on it.
    results = checks_mod.run_checks(trace, scenario.checks)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
