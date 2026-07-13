"""`agentd status` / `agentd stop` / `agentd restart` — daemon lifecycle controls."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    status = subparsers.add_parser("status", help="is the daemon running, where, which version")
    status.set_defaults(func=run_status)
    stop = subparsers.add_parser("stop", help="stop the background daemon")
    stop.set_defaults(func=run_stop)
    restart = subparsers.add_parser("restart", help="stop the daemon and start a fresh one")
    restart.set_defaults(func=run_restart)


def run_status(args: argparse.Namespace) -> int:
    from agentd import lifecycle

    info = lifecycle.find_running()
    if info is None:
        print("agentd: not running (start with `agentd` or `agentd serve`)")
        return 1
    print(
        f"agentd: running  pid {info.pid}  {info.ws_url}  "
        f"v{info.version or '?'}  since {info.started_at or '?'}  "
        f"auth {'on' if info.token else 'off'}"
    )
    try:
        from agentd.cli import rpc

        hello = rpc.call("hello", timeout=10, info=info)
        print(
            f"product: {hello.get('product', 'agentd')}  |  model: {hello.get('model', '?')}  |  "
            f"default agent: {hello.get('agentId', '?')}  |  "
            f"agents: {', '.join(a['id'] for a in hello.get('agents', []))}"
        )
    except Exception as e:  # noqa: BLE001 — status must not crash on a busy daemon
        print(f"(hello failed: {e})")
    return 0


def run_stop(args: argparse.Namespace) -> int:
    from agentd import lifecycle

    if lifecycle.find_running() is None:
        print("agentd: not running")
        return 0
    if lifecycle.stop_daemon():
        print("agentd: stopped")
        return 0
    print("agentd: could not stop the daemon (check `agentd status`)")
    return 1


def run_restart(args: argparse.Namespace) -> int:
    from agentd import lifecycle

    run_stop(args)
    try:
        info = lifecycle.spawn_daemon()
    except RuntimeError as e:
        print(f"could not start the daemon: {e}")
        return 1
    print(f"agentd: started (pid {info.pid}, {info.ws_url})")
    return 0
