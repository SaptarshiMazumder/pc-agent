"""The CLI — run a scenario live over a websocket, or re-analyse a saved trace.

Presentation only: argument parsing, stream encoding, exit codes. The drive loop lives in
`live_driver` (shared with the Agent Builder's `e2e_run` tool, which uses the in-process
transport instead of the socket this CLI dials); the analysis lives in `signals` + `checks` +
`report` and is identical on every path.

LIVE (true end-to-end — a real daemon, a real model, the real backend the settings point at):

    python -m agent_runtime.e2e.runner \
      --scenario agents/comfy-artchitect/e2e/civitai-style-i2v.json \
      --daemon wss://<daemon-host> --token <account-session-token> \
      --model <model-id> --set COMFYUI_URL=<live-url>

REPLAY (re-diagnose a saved trace — no daemon, no model, stdlib only):

    python -m agent_runtime.e2e.runner --replay path/to.trace.jsonl --scenario <scenario.json>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import checks as checks_mod
from . import report as report_mod
from . import signals
from .live_driver import WsGatewayTransport, drive
from .scenario import Scenario
from .trace import Trace, load_trace


def analyse(trace: Trace, scenario: Scenario | None) -> str:
    findings = signals.diagnose(trace)
    results = checks_mod.run_checks(trace, scenario.checks) if scenario else []
    return report_mod.render(trace, findings, results, goal=scenario.goal if scenario else "")


async def _live(scenario: Scenario, daemon: str, token: str, out: Path, model: str) -> Trace:
    async with WsGatewayTransport(daemon, token) as transport:
        return await drive(scenario, transport, out, model=model)


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
    trace = asyncio.run(_live(scenario, args.daemon, args.token, out, args.model))
    print(analyse(trace, scenario))
    print(f"\ntrace saved: {out}")
    # Non-zero exit if any check failed — so CI / a wrapper can gate on it.
    results = checks_mod.run_checks(trace, scenario.checks)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
