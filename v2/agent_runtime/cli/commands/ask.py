"""`agentd ask` — send ONE message to an agent, print what happened, exit.

THIS EXISTS SO A PROGRAM CAN DRIVE AN AGENT. `agentd chat` is a REPL: it opens a prompt and
waits for a human to type, which is exactly right for a person and useless to anything else —
called from a tool it simply hangs on stdin forever.

That gap had a cost out of all proportion to its size. Agent Builder can write an agent, run
`node --check` on its JavaScript, and validate its `agent.toml` — but it could never RUN the
agent it had just built, because there was no non-interactive way in. So it wrote files and
declared victory, and the first thing to actually execute the agent was the user, later,
finding it empty. Writing-and-hoping, one level up from the bug the `exec` grant fixed.

WHAT IT PRINTS IS THE POINT. Not just the reply: the TOOLS the agent called, in order, and
whether the run ended clean. "It answered plausibly" and "it answered without ever calling the
tool that fetches the data" look identical in prose and completely different here — and the
second is the failure that keeps shipping.
"""

from __future__ import annotations

import argparse
import asyncio


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "ask",
        help="send one message to an agent and print the reply (non-interactive; for scripts)",
    )
    parser.add_argument("message", help="what to say to the agent")
    parser.add_argument("--agent", default=None, help="agent id (default: main)")
    parser.add_argument(
        "--session",
        default=None,
        help="session key — reuse one to continue a conversation across calls "
        "(default: a fresh throwaway session, so runs cannot contaminate each other)",
    )
    parser.add_argument("--url", default=None, help="explicit gateway URL (skips auto-start)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="seconds to wait for the run to finish (default 300)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print only the reply, no tool trace"
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    # AGENT REPLIES CONTAIN EMOJI, and a Windows console is often cp932/cp1252 — printing one
    # raised UnicodeEncodeError and took the whole command down, turning "read what the agent
    # said" into a stack trace. Replace what the console cannot draw; never fail over a glyph.
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # not a reconfigurable stream (a pipe in a test)
            pass
    return asyncio.run(
        _ask(
            message=args.message,
            agent=args.agent,
            session=args.session,
            url=args.url,
            timeout=args.timeout,
            quiet=args.quiet,
        )
    )


async def _ask(
    message: str,
    agent: str | None,
    session: str | None,
    url: str | None,
    timeout: float,
    quiet: bool,
) -> int:
    """PRINTING only. The run itself lives in clients/one_shot_run so a TOOL can do the same
    thing without a shell — Agent Builder needs to run what it built, and on a source checkout
    there is no `agentd` on PATH to shell out to."""
    import sys

    from agent_runtime.clients.one_shot_run import run_once

    outcome = await run_once(
        message=message, agent=agent, session=session, url=url, timeout=timeout
    )
    if outcome.transport_error:
        print(outcome.transport_error)
        return 1

    print(outcome.reply or "(the agent produced no reply)")
    if outcome.error:
        # WHY it failed, in the provider's own words. Without this the caller sees
        # "⚠️ Agent couldn't generate a response" — true, useless, and identical for a missing
        # API key, a rate limit and a broken tool.
        print(f"\nRUN FAILED: {outcome.error.splitlines()[0]}", file=sys.stderr)
    if not quiet:
        # The trace goes to STDERR so `agentd ask ... > answer.txt` captures the answer alone
        # while a human (or a tool reading both streams) still sees what actually ran.
        print(
            f"\n--- tools called: {', '.join(outcome.tools) if outcome.tools else 'NONE'}"
            f"\n--- stop reason : {outcome.stop_reason or 'unknown'}",
            file=sys.stderr,
        )
    # EXIT CODE FOLLOWS THE RUN. `agentd ask ... && something` has to mean what it looks like,
    # and a caller looping on this needs to branch on failure without parsing prose.
    return 0 if outcome.ok else 1

