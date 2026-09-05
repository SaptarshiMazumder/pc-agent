"""run_agent — actually run the agent you just built, and read what it did.

WHY A TOOL AND NOT A COMMAND. The skill used to say "run `agentd ask --agent <id> …`". That is a
console script the wheel declares, so it exists in a packaged install and does NOT exist in a
source checkout — which is where agents are authored. A real build spent eleven `exec` calls
discovering `python -m agent_runtime.cli.main` before it could run its agent once, including a
try at `import agentd`, a package name that has not existed for a while.

A tool cannot be missing from PATH. Same daemon, same run, same output; no shell in the middle.

THE TOOLS LINE IS WHY THIS EXISTS. An agent that describes the work and an agent that does it
produce indistinguishable prose, and the list of tools it actually called is the only cheap way
to tell them apart.
"""

from __future__ import annotations

import time
import uuid

from agent_runtime.application.interfaces.tool import Tool, ToolResult

#: Long enough for a real agent turn with research in it; short enough that a hung run does not
#: hold the builder's own turn open indefinitely.
DEFAULT_TIMEOUT_S = 300


class RunAgentTool(Tool):
    name = "run_agent"
    label = "Run Agent"
    concurrency = "sequential"  # it runs another agent; do not fan these out
    default_timeout_sec = None  # the child run has its own timeout
    default_retryable = False  # side-effecting: it really runs the agent
    description = (
        "RUN AN AGENT AND SEE WHAT IT ACTUALLY DID. Send one message, get back its reply, the "
        "TOOLS it called, and how the run ended. Use it after building or changing an agent, "
        "before telling anyone it works — every check before this one proves the agent is "
        "well-FORMED; this is the only one that proves it WORKS.\n"
        "READ THE TOOLS LINE FIRST. These are the same agent to a reader and completely "
        "different products:\n"
        "  tools: get_cost_snapshot, compare_thresholds   -> it did the work\n"
        "  tools: NONE                                    -> it described the work\n"
        "An agent that answers plausibly while calling nothing is the failure that keeps "
        "shipping, and prose cannot show it to you.\n"
        "If the run failed, the reason comes back in the agent's own words (a missing key, an "
        "unconnected server, a crashing tool). Fix it and run again.\n"
        "Each call uses a FRESH session, so one test cannot lean on another's context — pass "
        "`session` explicitly only when you are deliberately testing a follow-up turn."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id", "message"],
        "properties": {
            "agent_id": {"type": "string", "description": "the agent to run"},
            "message": {
                "type": "string",
                "description": "what to say to it — something a REAL user would ask, not a test "
                "string. 'hello' proves the loop runs and nothing else",
            },
            "session": {
                "type": "string",
                "description": "reuse a session key to continue a conversation across calls. "
                "Omit for a fresh one (the default, and what you want for a test)",
            },
            "timeout_s": {
                "type": "integer",
                "description": f"seconds to wait for the run (default {DEFAULT_TIMEOUT_S})",
            },
        },
    }

    def __init__(self, ctx=None):
        # PluginContext, for `gateway_client` — the in-process transport. Optional so an
        # existing direct construction (tests) keeps working and falls back to the socket.
        self._ctx = ctx

    async def execute(self, tool_call_id, params, abort, on_update=None):
        agent_id = str(params.get("agent_id") or "").strip()
        message = str(params.get("message") or "").strip()
        if not agent_id or not message:
            return ToolResult.text("run_agent needs `agent_id` and `message`", is_error=True)

        if on_update:
            # A real turn takes tens of seconds. Without this the row sits motionless and reads
            # as a hang, which is when someone kills it.
            on_update(f"running {agent_id}…")

        session = str(params.get("session") or "") or None
        timeout = float(params.get("timeout_s") or DEFAULT_TIMEOUT_S)

        # IN-PROCESS FIRST. The old path dialed the daemon back over a fresh socket carrying
        # `?act_as=` — which authorises only on a machine-token daemon with accounts off, so on
        # a hosted daemon this tool simply failed (and cabbie had to deny it). The in-process
        # gateway client is the same chat.send with the caller's own account carried from the
        # run context — identical on desktop and hosted. The socket stays as the fallback for
        # constructions that have no PluginContext (unit tests, older wiring).
        thunk = getattr(self._ctx, "gateway_client", None)
        client = thunk() if callable(thunk) else None
        if client is not None:
            outcome = await _run_inprocess(client, agent_id, message, session, timeout)
        else:
            from agent_runtime.clients.one_shot_run import run_once
            from agent_runtime.infrastructure import accounts

            outcome = await run_once(
                message=message,
                agent=agent_id,
                session=session,
                timeout=timeout,
                act_as=accounts.account_id() or None,
            )

        if outcome.transport_error:
            # Never reached the agent at all — a different problem from the agent failing, and
            # reporting it as the agent's fault sends the author to fix working code.
            return ToolResult.text(
                f"could not run {agent_id}: {outcome.transport_error}", is_error=True
            )

        tools = ", ".join(outcome.tools) if outcome.tools else "NONE"
        lines = [
            f"{agent_id} — {'ok' if outcome.ok else 'FAILED'}",
            f"tools called: {tools}",
            f"stop reason : {outcome.stop_reason or 'unknown'}",
        ]
        if outcome.error:
            lines.append(f"error: {outcome.error}")
        lines.append("")
        lines.append(outcome.reply or "(the agent produced no reply)")

        if not outcome.tools and outcome.ok:
            # A clean run that touched nothing is the single most common way a finished-looking
            # agent turns out to be empty, and it does not announce itself anywhere else.
            lines.append(
                "\nNOTE: it called NO tools. If this message needed data, a file or an API, the "
                "agent described the work instead of doing it — check its [tools] allow list and "
                "whether its instructions actually tell it to use them."
            )
        return ToolResult.text("\n".join(lines), is_error=not outcome.ok)


async def _run_inprocess(client, agent_id: str, message: str, session: str | None,
                         timeout: float):
    """One message over the in-process gateway client, folded into the same RunOutcome the
    socket path produces — reply text, the tools called in order, how the run ended. The event
    stream is bound BEFORE chat.send so nothing can fall between starting the run and hearing it."""
    from agent_runtime.clients.one_shot_run import RunOutcome

    out = RunOutcome()
    session_key = session or f"ask-{uuid.uuid4().hex[:8]}"
    reply: list[str] = []
    stream = client.events(session_key)
    try:
        try:
            await client.send("chat.send", {"message": message, "sessionKey": session_key,
                                            "agentId": agent_id})
        except Exception as e:  # noqa: BLE001 — a refused send is a transport fact, not a crash
            out.transport_error = str(e)
            return out
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                out.transport_error = f"timed out after {timeout:g}s — the run did not finish"
                break
            event = await stream.next(remaining)
            if event is None:
                out.transport_error = f"timed out after {timeout:g}s — the run did not finish"
                break
            kind = event.get("type") or ""
            if kind == "tool_execution_start":
                out.tools.append(str(event.get("name") or event.get("toolName") or "?"))
            elif kind == "message_end":
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
    finally:
        stream.close()
    out.reply = "\n".join(reply).strip()
    return out
