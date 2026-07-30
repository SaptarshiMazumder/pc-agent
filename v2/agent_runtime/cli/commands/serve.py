"""`agentd serve` — run the gateway daemon in the FOREGROUND (dev / supervised).

Exactly `python -m agent_runtime`: logs to the console, Ctrl-C stops it. The detached
background daemon is what bare `agentd` / `agentd chat` auto-spawn instead."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("serve", help="run the gateway daemon in the foreground")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from agent_runtime.cli import first_run

    if not first_run.ensure_onboarded():
        return 1
    from agent_runtime.main.main import main as serve_main

    serve_main()
    return 0
