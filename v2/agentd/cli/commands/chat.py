"""`agentd chat` (and bare `agentd`) — attach the terminal REPL to the daemon,
spawning it in the background first when none is running."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "chat", help="chat with your agent (auto-starts the daemon; the default command)"
    )
    parser.add_argument("--agent", default=None, help="agent id to talk to")
    parser.add_argument("--session", default=None, help="session key to resume")
    parser.add_argument(
        "--project", default=None, help="project id — new chats in this REPL land in that project"
    )
    parser.add_argument("--url", default=None, help="explicit gateway URL (skips auto-start)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    return _attach(agent=args.agent, session=args.session, url=args.url, project=args.project)


def run_default() -> int:
    """Bare `agentd`: the product entry — onboard, ensure daemon, chat."""
    return _attach()


def _attach(
    agent: str | None = None,
    session: str | None = None,
    url: str | None = None,
    project: str | None = None,
) -> int:
    from agentd import lifecycle
    from agentd.cli import first_run

    if not first_run.ensure_onboarded():
        return 1
    if url is None:
        try:
            info, spawned = lifecycle.ensure_running()
        except RuntimeError as e:
            print(f"could not start the daemon: {e}")
            return 1
        if spawned:
            print(f"started agentd daemon (pid {info.pid}, {info.ws_url})")
        url = info.connect_url()
    from agentd.clients.terminal.__main__ import main as terminal_main

    argv = ["--url", url]
    if agent:
        argv += ["--agent", agent]
    if session:
        argv += ["--session", session]
    if project:
        argv += ["--project", project]
    terminal_main(argv)
    return 0
