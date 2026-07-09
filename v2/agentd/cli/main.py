"""CLI entrypoint: build the parser from the command registry and dispatch."""

from __future__ import annotations

import argparse
import sys

from agentd import __version__
from agentd.cli import commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentd",
        description="agentd — your agents, one daemon. Bare `agentd` starts (or attaches to) "
        "the daemon and opens the chat REPL.",
    )
    parser.add_argument("--version", action="version", version=f"agentd {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for module in commands.ALL:
        module.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:  # bare `agentd` -> onboard + daemon + chat
        from agentd.cli.commands import chat

        return chat.run_default()
    try:
        return int(func(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
