"""`agentd agents list` — the installed agents. Live from the daemon when running
(same list every client sees), offline via the file registry otherwise."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("agents", help="list installed agents")
    sub = parser.add_subparsers(dest="agents_command")
    list_parser = sub.add_parser("list", help="list installed agents")
    list_parser.set_defaults(func=run_list)
    parser.set_defaults(func=run_list)


def run_list(args: argparse.Namespace) -> int:
    from agent_runtime.cli import rpc

    try:
        payload = rpc.call("agents.list", timeout=15)
        default = payload.get("default", "main")
        for agent in payload.get("agents", []):
            marker = "*" if agent["id"] == default else " "
            print(f" {marker} {agent['id']:<24} {agent.get('name', '')}")
        print("\n(live from the running daemon; * = default)")
        return 0
    except rpc.DaemonNotRunning:
        pass
    from agent_runtime.config import load_config
    from agent_runtime.infrastructure.agents import FileAgentRegistry

    config = load_config()
    registry = FileAgentRegistry(config)
    default = getattr(config, "agent_id", "main")
    for agent_id in registry.list_ids():
        marker = "*" if agent_id == default else " "
        print(f" {marker} {agent_id:<24} {registry.get(agent_id).name}")
    print("\n(offline from the file registry; * = default)")
    return 0
