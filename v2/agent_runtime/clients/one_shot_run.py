"""Run ONE message against an agent and report what actually happened.

THE MECHANISM BEHIND `agentd ask`, extracted so it is not only a command.

Agent Builder has to run the agents it writes — that is the difference between writing-and-hoping
and write-check-fix. It was told to shell out to `agentd ask`, which is right in a packaged
install and wrong in a source checkout, where the console script was never generated: observed in
the wild, a build spent eleven `exec` calls discovering `python -m agent_runtime.cli.main` before
it could run the agent once. A tool cannot be missing from PATH, so this is a function first and
a command second.

WHAT IT REPORTS IS THE POINT. Not just the reply: the TOOLS the agent called, in order, and how
the run ended. "It answered plausibly" and "it answered without ever calling the tool that fetches
the data" read identically in prose and differ completely here — and the second is the failure
that keeps shipping.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field


@dataclass
class RunOutcome:
    """One run, as facts rather than console output."""

    reply: str = ""
    tools: list[str] = field(default_factory=list)
    stop_reason: str = ""
    #: The provider's own sentence when the run failed ("Missing GEMINI_API_KEY"). The difference
    #: between fixing it and guessing at it.
    error: str = ""
    #: Set when the run never started — no daemon, timeout, refused request. Distinct from
    #: `error`, which is the agent's run failing: one is about reaching it, the other about it.
    transport_error: str = ""

    @property
    def ok(self) -> bool:
        return not (self.error or self.transport_error or self.stop_reason == "error")


async def run_once(
    message: str,
    agent: str | None = None,
    session: str | None = None,
    url: str | None = None,
    act_as: str | None = None,
    timeout: float = 300.0,
) -> RunOutcome:
    """Send one message over the daemon's socket and collect the result.

    A FRESH session by default. Reusing one silently carries the previous run's context into this
    one, which turns "does this agent work?" into "does it work given whatever I asked it last
    time" — and those answers differ exactly when it matters.
    """
    import websockets

    from agent_runtime import lifecycle

    out = RunOutcome()
    if url is None:
        try:
            info, _spawned = lifecycle.ensure_running()
        except RuntimeError as e:
            out.transport_error = f"could not start the daemon: {e}"
            return out
        url = info.connect_url()
    # THE CALLER'S TENANCY, carried onto this fresh socket. Without it a child run resolves
    # against the shared catalogue only, and every account-layer agent is "unknown" — see the
    # act_as note in the gateway's connection handler.
    if act_as:
        url += ("&" if "?" in url else "?") + "act_as=" + act_as

    session_key = session or f"ask-{uuid.uuid4().hex[:8]}"
    reply: list[str] = []
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
                        out.transport_error = str((frame.get("payload") or {}).get("error") or "")
                        return out
                    if frame.get("type") != "event" or frame.get("event") != "chat.event":
                        continue
                    # The run event is NESTED inside the frame's payload, and flattened to
                    # {"type": <name>, **payload} by AgentEvent.to_dict.
                    event = (frame.get("payload") or {}).get("event") or {}
                    kind = event.get("type") or ""
                    if kind == "tool_execution_start":
                        out.tools.append(str(event.get("name") or event.get("toolName") or "?"))
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
                        out.stop_reason = str(event.get("stopReason") or "")
                        out.error = str(event.get("error") or "").strip()
                        break
    except TimeoutError:
        out.transport_error = f"timed out after {timeout:g}s — the run did not finish"
    except OSError as e:
        out.transport_error = f"could not reach the daemon at {url}: {e}"

    out.reply = "\n".join(reply).strip()
    return out
