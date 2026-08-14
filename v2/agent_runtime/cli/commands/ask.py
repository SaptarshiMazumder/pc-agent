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
    import json
    import uuid

    import websockets

    from agent_runtime import lifecycle

    if url is None:
        try:
            info, _spawned = lifecycle.ensure_running()
        except RuntimeError as e:
            print(f"could not start the daemon: {e}")
            return 1
        url = info.connect_url()

    # A FRESH session by default. Reusing one silently carries the previous run's context into
    # this one, which turns "does this agent work?" into "does it work given whatever I asked it
    # last time" — the two answers differ exactly when it matters.
    session_key = session or f"ask-{uuid.uuid4().hex[:8]}"
    tools: list[str] = []
    reply: list[str] = []
    stop_reason = ""
    error = ""

    try:
        async with websockets.connect(url, max_size=32 * 1024 * 1024) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": uuid.uuid4().hex[:12],
                        "method": "chat.send",
                        "params": {
                            "message": message,
                            "sessionKey": session_key,
                            **({"agentId": agent} if agent else {}),
                        },
                    }
                )
            )
            async with asyncio.timeout(timeout):
                async for raw in ws:
                    frame = json.loads(raw)
                    if frame.get("type") == "res" and not frame.get("ok", True):
                        print(f"error: {(frame.get('payload') or {}).get('error')}")
                        return 1
                    if frame.get("type") != "event" or frame.get("event") != "chat.event":
                        continue
                    # The run event is NESTED inside the frame's payload, and flattened to
                    # {"type": <name>, **payload} by AgentEvent.to_dict.
                    event = (frame.get("payload") or {}).get("event") or {}
                    kind = event.get("type") or ""
                    if kind == "tool_execution_start":
                        tools.append(str(event.get("name") or event.get("toolName") or "?"))
                    elif kind == "message_end":
                        # The assistant's text lives in message.content blocks, not on the event.
                        msg = event.get("message") or {}
                        if msg.get("role") == "assistant":
                            for block in msg.get("content") or ():
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text = str(block.get("text") or "").strip()
                                    if text:
                                        reply.append(text)
                    elif kind == "agent_end":
                        stop_reason = str(event.get("stopReason") or "")
                        # WHY it failed, not just that it did. A caller iterating on an agent
                        # needs the provider's own sentence ("Missing GEMINI_API_KEY") — that is
                        # the difference between fixing it and guessing at it.
                        error = str(event.get("error") or "").strip()
                        break
    except TimeoutError:
        print(f"timed out after {timeout:g}s — the run did not finish")
        return 1
    except OSError as e:
        print(f"could not reach the daemon at {url}: {e}")
        return 1

    answer = "\n".join(reply).strip()
    print(answer or "(the agent produced no reply)")
    if error:
        # WHY it failed, in the provider's own words. Without this the caller sees
        # "⚠️ Agent couldn't generate a response" — true, useless, and identical for a missing
        # API key, a rate limit and a broken tool.
        import sys

        print(f"\nRUN FAILED: {error.splitlines()[0]}", file=sys.stderr)
    if not quiet:
        # The trace goes to STDERR so `agentd ask ... > answer.txt` captures the answer alone
        # while a human (or a tool reading both streams) still sees what actually ran.
        import sys

        print(
            f"\n--- tools called: {', '.join(tools) if tools else 'NONE'}"
            f"\n--- stop reason : {stop_reason or 'unknown'}",
            file=sys.stderr,
        )
    # EXIT CODE FOLLOWS THE RUN. `agentd ask ... && something` has to mean what it looks like,
    # and a caller looping on this needs to branch on failure without parsing prose.
    return 1 if (error or stop_reason == "error") else 0

