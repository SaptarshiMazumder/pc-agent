"""WebSocket gateway: receives chat.send, runs the agent, broadcasts events.

Mirrors the reference gateway's chat.send semantics:
- respond immediately with {runId}; the run executes async
- one active run per session (busy -> error response)
- idempotencyKey dedupe
- loop events broadcast to all connected clients as chat.event frames
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import time
from pathlib import Path
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import websockets
from websockets.asyncio.server import ServerConnection, serve

from agentd import __version__, lifecycle

from agentd.application.run_context import current_run_context, take_run_outcome
from agentd.application.services.agent_service import AgentService
from agentd.config import Config
from agentd.domain.agent import RunMode, agent_id_from_session_key, cron_session_key
from agentd.domain.autonomy import ScheduledTask, resolve_run_outcome
from agentd.domain.events import AgentEvent
from agentd.domain.notify import Notification
from agentd.infrastructure.memory.local_store import list_sessions
from agentd.presentation.protocol import Event, ProtocolError, Request, Response, dump_frame, parse_frame

log = logging.getLogger("agentd")


def _effective_model(config) -> str:
    """The reasoning model as the models layer resolves it (CONFIG-ONLY) — for display/status. Shows
    "(CONFIG MISSING)" instead of crashing when no agentd.config.json was loaded. When cost-efficiency
    routing is ON, the static brain id alone is MISLEADING (text turns actually run the cheap text_model,
    only image turns use the vision_model), so reflect the routing here — this is the banner the user
    reads to know which model is really doing the work."""
    from agentd.application.tool_models import ConfigMissingError, brain_model
    try:
        base = brain_model(config)
    except ConfigMissingError:
        return "(CONFIG MISSING)"
    ce = getattr(config, "cost_efficiency", None) or {}
    if isinstance(ce, dict) and ce.get("enabled") and (ce.get("text_model") or ce.get("vision_model")):
        text = ce.get("text_model") or base
        vision = ce.get("vision_model") or base
        if text != vision:
            return f"{text} -> {vision} on images (cost-efficiency)"
        return f"{text} (cost-efficiency)"
    return base


# sessions.history DISPLAY caps: a full transcript re-sends every tool dump + inline
# base64 image, which can be MEGABYTES (blows the WS message limit, and the client
# renders none of the image bytes anyway). We trim to a display-shaped transcript —
# generous text, bounded tool output, image DATA dropped (kept as a marker). The live
# view already shows a first-line preview + expandable; history matches that intent.
_HISTORY_TOOL_RESULT_CAP = 4000
_HISTORY_THINKING_CAP = 8000
_HISTORY_TEXT_CAP = 40000
_HISTORY_ARG_CAP = 2000


def _cap(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n…[+{len(text) - limit} chars]"


def _trim_history_block(block: dict) -> dict:
    """Trim ONE content block for display. Image bytes are dropped (marker kept); text/
    thinking are capped; tool-call args with huge string values are capped."""
    kind = block.get("type")
    if kind == "text":
        return {"type": "text", "text": _cap(block.get("text", ""), _HISTORY_TEXT_CAP)}
    if kind == "thinking":
        return {"type": "thinking", "thinking": _cap(block.get("thinking", ""), _HISTORY_THINKING_CAP)}
    if kind == "image":
        return {"type": "image", "data": "", "mimeType": block.get("mimeType", ""), "elided": True}
    if kind == "toolCall":
        args = block.get("arguments") or {}
        trimmed = {k: (_cap(v, _HISTORY_ARG_CAP) if isinstance(v, str) else v) for k, v in args.items()}
        return {"type": "toolCall", "id": block.get("id", ""), "name": block.get("name", ""),
                "arguments": trimmed}
    return block


def _trim_history_message(message: dict) -> dict:
    """Trim one wire-form message for the sessions.history payload (keeps it KB, not MB)."""
    role = message.get("role")
    ts = message.get("ts", "")          # the line's stored send time (ISO) — kept for display
    if role == "user":
        return {"role": "user", "content": _cap(message.get("content", ""), _HISTORY_TEXT_CAP),
                "ts": ts, "timestamp": message.get("timestamp", 0)}
    if role == "assistant":
        return {"role": "assistant",
                "content": [_trim_history_block(b) for b in message.get("content") or []],
                "stopReason": message.get("stopReason", "stop"),
                "errorMessage": message.get("errorMessage"),
                "ts": ts, "timestamp": message.get("timestamp", 0)}
    if role == "toolResult":
        return {"role": "toolResult", "toolCallId": message.get("toolCallId", ""),
                "toolName": message.get("toolName", ""),
                "content": [{"type": "text",
                             "text": _cap("".join(b.get("text", "") for b in message.get("content") or []
                                                  if b.get("type") == "text"), _HISTORY_TOOL_RESULT_CAP)}],
                "isError": message.get("isError", False),
                "ts": ts, "timestamp": message.get("timestamp", 0)}
    return message


# The prompt posted on an autonomous heartbeat tick (no user message). The agent's
# HEARTBEAT.md checklist is assembled into the system prompt for heartbeat runs.
HEARTBEAT_PROMPT = (
    "This is an autonomous heartbeat tick — no user is present. Read your HEARTBEAT.md "
    "checklist (above) and act on anything that needs attention using your tools. When "
    "done, call heartbeat_respond exactly once with the outcome; if nothing needed "
    "attention, use outcome='nothing-to-do' and notify=false."
)

# A scheduled `deliver=message` task: the agent outputs the stored text verbatim
# (reuses the normal run/stream path — no separate delivery plumbing).
OUTBOX_PROMPT = (
    "Deliver the following message to the user verbatim — output it exactly as written, "
    "with no preamble, summary, or additions:\n\n{text}"
)


@dataclass
class RunHandle:
    run_id: str
    session_key: str
    abort: asyncio.Event
    client_id: str | None = None  # the client connection that started this run
    parent_session_key: str | None = None  # set for a SUB-AGENT run -> its progress is
    #                                         relayed (compactly) to the parent's view
    task: asyncio.Task | None = None
    cron_run_id: str | None = None  # set for cron runs -> recorded in the run history
    cron_task_id: str | None = None  # the cron job's id (for failure-alert escalation, S14)
    cron_failure_alert: int = 0      # auto-pause + alert after N consecutive failures (0=off)


def subagent_relay(child_session_key: str, event: AgentEvent) -> AgentEvent | None:
    """Compact ONE child-run event into a single `subagent_event` for the PARENT's view —
    only the meaningful beats: start, each tool the child runs, and done/error. Raw text /
    thinking deltas are dropped (relaying them would flood the parent, especially with several
    children running at once). Returns None to skip. The client renders these dimmed/indented
    so a parent run shows its sub-agents working instead of going silent."""
    child = agent_id_from_session_key(child_session_key)
    if event.type == "agent_start":
        return AgentEvent("subagent_event", {"childAgent": child, "kind": "start"})
    if event.type == "tool_execution_start":
        return AgentEvent("subagent_event", {"childAgent": child, "kind": "tool",
                                             "tool": event.payload.get("toolName", "")})
    if event.type == "agent_end":
        err = event.payload.get("error")
        return AgentEvent("subagent_event", {
            "childAgent": child, "kind": "error" if err else "done",
            "detail": err or event.payload.get("stopReason", "")})
    return None


def _subagent_depth(session_key: str) -> int:
    """How many sub-agent levels deep a session is. 0 = a top-level run. Child keys encode the
    level as ``agent:<id>:sub:<depth>:<hex>``; an older flat ``...:sub:<hex>`` counts as 1.
    cron/channel keys (no ``sub`` segment) are depth 0."""
    parts = session_key.split(":")
    if "sub" not in parts:
        return 0
    nxt = parts[parts.index("sub") + 1] if parts.index("sub") + 1 < len(parts) else ""
    return int(nxt) if nxt.isdigit() else 1


def _guarded_with_source(tools, config) -> list:
    """Wrap tools in GuardedTool AND stamp their catalog ``source`` (mcp:<server> for the
    namespaced MCP tools that flow through here). Mirrors the container's wrap step."""
    from agentd.application.services.agent_service import tool_source
    from agentd.infrastructure.tools.guard import GuardedTool, resolve_policy
    out = []
    for t in tools:
        gt = GuardedTool(t, resolve_policy(config, t))
        gt.source = tool_source(t)
        out.append(gt)
    return out


def _server_dict(s) -> dict:
    """Serialize an McpServerConfig back to a JSON-config dict (omit empty fields)."""
    out: dict = {"name": getattr(s, "name", "")}
    for k in ("transport", "command", "env", "url", "headers"):
        v = getattr(s, k, None)
        if v:
            out[k] = v
    return out


def _persist_mcp_servers(config) -> bool:
    """Write ``config.mcp_servers`` back to agentd.config.json (so a hot-add survives restart).
    Preserves every other key in the file. Best-effort: a write failure is logged, not fatal."""
    import json
    import os
    from pathlib import Path

    from agentd.config import V2_ROOT
    path = None
    for cand in (os.environ.get("AGENTD_CONFIG"), "agentd.config.json",
                 str(V2_ROOT / "agentd.config.json")):
        if cand and Path(cand).is_file():
            path = Path(cand)
            break
    if path is None:
        path = V2_ROOT / "agentd.config.json"          # create at the default location
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data["mcp_servers"] = [_server_dict(s) for s in (config.mcp_servers or [])]
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001 — persistence is best-effort
        log.warning("could not persist mcp_servers to %s: %s", path, e)
        return False


def _persist_webhooks(config) -> bool:
    """Write ``config.webhooks`` back to agentd.config.json (so a created hook survives restart).
    Preserves every other key. Best-effort: a write failure is logged, not fatal."""
    import json
    import os
    from pathlib import Path

    from agentd.config import V2_ROOT
    path = None
    for cand in (os.environ.get("AGENTD_CONFIG"), "agentd.config.json",
                 str(V2_ROOT / "agentd.config.json")):
        if cand and Path(cand).is_file():
            path = Path(cand)
            break
    if path is None:
        path = V2_ROOT / "agentd.config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data["webhooks"] = list(config.webhooks or [])
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001 — persistence is best-effort
        log.warning("could not persist webhooks to %s: %s", path, e)
        return False


@dataclass
class Gateway:
    """Transport only: accepts WebSocket frames and delegates work to the injected
    ``service`` (the AgentService use-case). It is built by main/container.py — it no
    longer composes anything itself."""

    config: Config
    service: AgentService                          # injected use-case (does the work)
    browser_manager: object | None = None          # injected; closed on shutdown
    mcp_provider: object | None = None             # injected; discovered at startup, closed on shutdown
    registry: object | None = None                 # injected; the agent registry (for the scheduler)
    task_store: object | None = None               # injected; durable cron ledger (Phase 2b), or None
    memory_bank: object | None = None               # injected; long-term memory store (S4), or None
    event_log: object | None = None                 # injected; durable per-run event stream, or None
    credential_store: object | None = None          # injected; login vault (/connect form writes here)
    connect_tokens: object | None = None            # injected; one-time /connect-link tokens
    safe_to_send_gate: object | None = None         # injected; out-of-band privacy gate on channel replies
    notifier: object | None = None                  # built in serve(); outbound user notifications (5a)
    channels: list = field(default_factory=list)    # active messaging channels (5b), built in serve()
    channel_notifiers: list = field(default_factory=list)  # ChannelNotifier per notify-capable channel
    subagent_active: int = 0                         # in-flight sub-agent runs (runaway guard, S8)
    webhook_server: object | None = None             # the WebhookServer (set in serve); hosts task hooks
    # M2 auth: the bearer token clients must present ("" => open, the test/dev default).
    # Set by serve() from config (gateway_auth/gateway_token) — never at construction, so
    # unit tests that drive _handle_conn directly are unaffected.
    auth_token: str = ""
    # M4: the marketplace service — built lazily on the first marketplace.* call
    # (mirrors _ensure_mcp_provider), wired to broadcast progress + hot-reload.
    marketplace: object | None = None
    clients: set[ServerConnection] = field(default_factory=set)
    runs: dict[str, RunHandle] = field(default_factory=dict)  # session_key -> handle
    idempotency: dict[str, str] = field(default_factory=dict)  # key -> run_id

    # ------------------------------------------------------------------ serve

    async def serve(self) -> None:
        # M2: ONE daemon per user — a live rendezvous file means another gateway owns
        # this machine's agentd; refuse loudly instead of fighting over ports/state.
        existing = lifecycle.find_running()
        if existing is not None and existing.pid != os.getpid():
            raise SystemExit(
                f"agentd is already running (pid {existing.pid}, {existing.ws_url}) — "
                f"attach with `agentd chat` or stop it with `agentd stop`.")
        # M2 auth: mint (or adopt) the bearer token clients must present. The token
        # travels ONLY via the 0600 rendezvous file — never argv, never logs.
        if getattr(self.config, "gateway_auth", False):
            self.auth_token = getattr(self.config, "gateway_token", "") or lifecycle.mint_token()
        # Fast, in-process registrations happen BEFORE bind (cheap, chat depends on them)…
        self._build_subagents()           # the spawn_subagent tool (S8), if enabled
        self._build_agent_messaging()     # message_agent: talk to OTHER persistent agents (A5)
        self._build_add_mcp()             # add_mcp: connect an MCP server by chatting (B2)
        # …but everything SLOW or external is deferred until AFTER the port is open (see
        # _deferred_startup): a cold external MCP server (uvx download, OAuth dance) used to
        # hold the bind for minutes, which stalls every client and the desktop supervisor.
        # Clients can chat with native tools immediately; MCP tools join the catalog live.
        scheduler_task = poller_task = webhook_task = None
        startup_task: asyncio.Task | None = None

        def _adopt_background(tasks: tuple) -> None:
            nonlocal scheduler_task, poller_task, webhook_task
            scheduler_task, poller_task, webhook_task = tasks

        async with serve(self._handle_conn, self.config.host, self.config.port):
            lifecycle.write_gateway_file(lifecycle.GatewayInfo(
                host=self.config.host, port=self.config.port, pid=os.getpid(),
                token=self.auth_token, version=__version__,
                started_at=datetime.now().isoformat(timespec="seconds")))
            log.info("listening on ws://%s:%s (auth %s)", self.config.host, self.config.port,
                     "on" if self.auth_token else "off")
            print(f"agentd listening on ws://{self.config.host}:{self.config.port}")
            print(f"model: {_effective_model(self.config)} | workspace: {self.config.workspace}")
            startup_task = asyncio.create_task(
                self._deferred_startup(_adopt_background), name="deferred-startup")
            try:
                await asyncio.Future()  # run forever
            finally:
                lifecycle.clear_gateway_file(only_pid=os.getpid())
                if startup_task is not None:
                    startup_task.cancel()
                if scheduler_task is not None:
                    scheduler_task.cancel()
                if poller_task is not None:
                    poller_task.cancel()
                if webhook_task is not None:
                    webhook_task.cancel()
                if self.task_store is not None:
                    self.task_store.close()
                if self.event_log is not None:
                    self.event_log.close()
                if self.browser_manager is not None:
                    await self.browser_manager.close()
                if self.mcp_provider is not None:
                    await self.mcp_provider.aclose()

    async def _deferred_startup(self, adopt_background) -> None:
        """Everything that used to run before bind but doesn't have to: connect external
        MCP servers (slow, cold-start-prone), then the pieces that depend on their tools
        (channels, notifier), then the background services. Order preserved exactly;
        only the bind moved earlier. ``adopt_background`` hands the started tasks back
        to serve() so shutdown still cancels them."""
        try:
            await self._discover_mcp_tools()  # connect external MCP servers, add their tools
            self._build_channels()            # messaging channels (5b) — email needs MCP tools first
            self._build_notifier()            # outbound notifications (client-push + durable + channels)
            adopt_background((
                self._start_scheduler(),      # autonomy (heartbeat); None if disabled
                self._start_channel_poller(),  # inbound poll channels; None if none
                self._start_webhook_server(),  # push channels (LINE) + task hooks (/hook/<id>)
            ))
            self._build_create_webhook()      # create_webhook: mint task triggers by chatting (D)
            # fill in any missing agent taglines/suggestions (one-time, per agent)
            asyncio.create_task(self._maybe_generate_presentations(),
                                name="agent-presentation")
            log.info("deferred startup complete (MCP + channels + background services)")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — startup extras must never kill the gateway
            log.exception("deferred startup failed — core chat keeps serving")

    def _start_scheduler(self):
        """Start the shared heartbeat scheduler — only when autonomy is enabled and a
        registry is available. Returns the task (cancelled on shutdown), or None."""
        if not getattr(self.config, "autonomy_enabled", False) or self.registry is None:
            return None
        from agentd.infrastructure.autonomy import HeartbeatScheduler

        scheduler = HeartbeatScheduler(
            self.registry, self._post_heartbeat, enabled=True,
            default_interval=self.config.heartbeat_default_interval,
            active_hours=self.config.heartbeat_active_hours,
            task_store=self.task_store, fire_task=self._post_cron,   # cron (2b)
        )
        return asyncio.create_task(scheduler.run(), name="autonomy-scheduler")

    # ------------------------------------------------------------- channels (5b)

    def _build_channels(self) -> None:
        """Build messaging channels from config (default none). Email channels invoke
        the Gmail MCP via _invoke_tool. A channel with `notify_to` also becomes a
        ChannelNotifier so notifications reach you on it (reuses the one transport)."""
        cfgs = getattr(self.config, "channels", None) or []
        if not cfgs:
            return
        from agentd.infrastructure.channels import build_channel
        from agentd.infrastructure.notify import ChannelNotifier

        for c in cfgs:
            try:
                ch = build_channel(c, self._invoke_tool)
            except Exception:  # noqa: BLE001 — a bad channel never blocks serving
                log.warning("failed to build channel %s", c, exc_info=True)
                continue
            if ch is None:
                continue
            self.channels.append(ch)
            notify_to = (c.get("notify_to") or "").strip()
            if notify_to:
                self.channel_notifiers.append(ChannelNotifier(ch, notify_to))
            log.info("channel ready: %s -> agent %s", ch.name, getattr(ch, "agent_id", "?"))

    def _build_subagents(self) -> None:
        """Register the spawn_subagent tool (S8) when enabled — the agent can delegate a
        subtask to a fresh child run and get its result back."""
        if not getattr(self.config, "subagents_enabled", False):
            return
        from agentd.infrastructure.tools.guard import GuardedTool, resolve_policy
        from agentd.infrastructure.tools.subagent_tool import SpawnSubagentTool

        tool = SpawnSubagentTool(self._spawn_subagent)
        self.service.add_tools([GuardedTool(tool, resolve_policy(self.config, tool))])
        log.info("sub-agents enabled (max %d concurrent, max depth %d)",
                 getattr(self.config, "subagent_max", 4),
                 min(5, max(1, int(getattr(self.config, "subagent_max_depth", 1) or 1))))

    def _build_agent_messaging(self) -> None:
        """Register message_agent (A5) when enabled — call ANOTHER persistent agent and get its
        reply (its own ongoing session, so it remembers). Gated by agent_messaging_enabled."""
        if not getattr(self.config, "agent_messaging_enabled", False):
            return
        from agentd.infrastructure.tools.guard import GuardedTool, resolve_policy
        from agentd.infrastructure.tools.message_agent_tool import MessageAgentTool

        tool = MessageAgentTool(self._message_agent)
        self.service.add_tools([GuardedTool(tool, resolve_policy(self.config, tool))])
        log.info("agent-to-agent messaging enabled (message_agent)")

    def _build_add_mcp(self) -> None:
        """Register add_mcp (B2) when enabled — the agent connects an MCP server by chatting
        (wraps the same _mcp_add machinery the mcp.add RPC uses). Gated by mcp_workshop."""
        if not getattr(self.config, "mcp_workshop", False):
            return
        from agentd.infrastructure.tools.add_mcp_tool import AddMcpTool
        from agentd.infrastructure.tools.guard import GuardedTool, resolve_policy

        tool = AddMcpTool(self._mcp_add)
        self.service.add_tools([GuardedTool(tool, resolve_policy(self.config, tool))])
        log.info("add_mcp enabled (agent can connect MCP servers)")

    async def _message_agent(self, target_id: str, message: str) -> str:
        """Run a turn on ANOTHER agent's PERSISTENT peer session and return its reply (A5).

        Distinct from _spawn_subagent: the target runs on a durable ``agent:<target>:peer:<caller>``
        session (so it accumulates state with this caller), as ITS own agent (identity/workspace/
        skills). Honors the caller's ``[subagents] allow`` scope; one-level only (loop guard)."""
        ctx = current_run_context()
        caller = (ctx.agent_id if ctx else None) or "main"
        parent_key = ctx.session_key if ctx else ""
        if ":peer:" in parent_key:                       # a messaged agent can't chain further
            return "agent-to-agent messaging cannot chain further (loop guard)."
        if not target_id:
            return "message_agent needs a target agent id."
        if target_id == caller:
            return "cannot message yourself — just do the work, or use spawn_subagent."
        if self.registry is not None and target_id not in self.registry.list_ids():
            return f"unknown agent: {target_id}"
        if self.registry is not None:                    # honor the caller's delegation allowlist
            from agentd.domain.agent import _matches
            try:
                spec = self.registry.get(caller)
            except KeyError:
                spec = None
            allow = getattr(spec, "subagents_allow", None)
            if allow is not None and not any(_matches(target_id, p) for p in allow):
                return (f"'{caller}' may not message '{target_id}' "
                        f"(allowed: {', '.join(allow) or 'none'}).")
        session_key = f"agent:{target_id}:peer:{caller}"
        handle = RunHandle(run_id=uuid.uuid4().hex[:12], session_key=session_key,
                           abort=asyncio.Event(), client_id=None,
                           parent_session_key=parent_key or None)   # relay progress to the caller
        await asyncio.create_task(
            self._run(handle, message, mode=RunMode.INTERACTIVE, agent_id=target_id))
        return self._last_answer(target_id, session_key) or "(the agent produced no reply)"

    async def _spawn_subagent(self, agent_id: str | None, task: str) -> str:
        """Run a self-contained CHILD agent turn and return its final answer. Called from
        within the parent's tool execution; the child runs as its own asyncio.Task so its
        run-context (contextvar) never clobbers the parent's. Capped + depth-limited."""
        ctx = current_run_context()
        parent_agent = (ctx.agent_id if ctx else None) or "main"
        parent_key = ctx.session_key if ctx else ""
        # Depth limit (A3): configurable nesting (1 = no nesting), hard ceiling 5. A run already
        # at max depth cannot spawn further — mirrors OpenClaw's maxSpawnDepth.
        max_depth = min(5, max(1, int(getattr(self.config, "subagent_max_depth", 1) or 1)))
        depth = _subagent_depth(parent_key)
        if depth >= max_depth:
            return (f"sub-agents cannot spawn deeper here (already at depth {depth}, "
                    f"max {max_depth}).")
        cap = int(getattr(self.config, "subagent_max", 4))
        if self.subagent_active >= cap:
            return f"sub-agent limit reached ({cap} concurrent); try again when some finish."

        child_agent = (agent_id or parent_agent)
        if self.registry is not None and child_agent not in self.registry.list_ids():
            return f"unknown agent: {child_agent}"
        # Allowlist (A4): when delegating to a NAMED other agent, honor the caller's [subagents]
        # allow scope (ids/globs). None => unrestricted; spawning oneself is always allowed.
        if agent_id and child_agent != parent_agent and self.registry is not None:
            from agentd.domain.agent import _matches
            try:
                spec = self.registry.get(parent_agent)
            except KeyError:
                spec = None
            allow = getattr(spec, "subagents_allow", None)
            if allow is not None and not any(_matches(child_agent, p) for p in allow):
                return (f"'{parent_agent}' may not delegate to '{child_agent}' "
                        f"(allowed: {', '.join(allow) or 'none'}).")
        session_key = f"agent:{child_agent}:sub:{depth + 1}:{uuid.uuid4().hex[:8]}"
        handle = RunHandle(run_id=uuid.uuid4().hex[:12], session_key=session_key,
                           abort=asyncio.Event(), client_id=None,
                           parent_session_key=parent_key or None)  # relay progress to parent
        self.subagent_active += 1
        try:
            # own Task => its own copied context => child set_run_context can't leak to parent
            await asyncio.create_task(
                self._run(handle, task, mode=RunMode.INTERACTIVE, agent_id=child_agent))
        finally:
            self.subagent_active -= 1
        return self._last_answer(child_agent, session_key) or "(sub-agent produced no answer)"

    def _start_channel_poller(self):
        """One shared loop polling every channel for inbound messages. None if no channels."""
        if not self.channels:
            return None
        from agentd.infrastructure.channels import ChannelPoller

        poller = ChannelPoller(
            self.channels, self._fire_channel,
            interval=float(getattr(self.config, "channel_poll_seconds", 15.0)))
        return asyncio.create_task(poller.run(), name="channel-poller")

    def _start_webhook_server(self):
        """One HTTP server hosting: PUSH channels (LINE etc.) on their own paths, the generic
        TASK-trigger route ``/hook/<id>`` (D), and the /connect form. Channel events fire through
        ``_fire_channel`` (conversational); task hooks run an agent via ``_run_task`` (no reply).
        None if nothing needs the server."""
        push = [c for c in self.channels if getattr(c, "webhook_path", None)]
        connect_on = self.credential_store is not None and self.connect_tokens is not None
        task_hooks_on = bool(getattr(self.config, "webhooks", None)) or \
            bool(getattr(self.config, "webhook_workshop", False))
        if not push and not connect_on and not task_hooks_on:   # nothing needs the HTTP server
            return None
        from agentd.infrastructure.channels.webhook import WebhookServer

        server = WebhookServer(
            push, self._fire_channel,
            host=getattr(self.config, "webhook_host", "0.0.0.0"),
            port=int(getattr(self.config, "webhook_port", 8788)),
            credential_store=self.credential_store if connect_on else None,
            connect_tokens=self.connect_tokens if connect_on else None,
            run_task=self._run_task if task_hooks_on else None,
            hooks=list(getattr(self.config, "webhooks", None) or []))
        self.webhook_server = server             # so create_webhook can add hooks live
        return asyncio.create_task(server.run(), name="webhook-server")

    async def _run_task(self, agent_id: str, task: str) -> None:
        """Run an agent with a one-off task from an external trigger (a webhook). Fire-and-forget
        on a dedicated ``agent:<id>:hook:<run>`` session — the agent acts (and can notify/cron if
        it needs to); no conversational reply is returned to the caller."""
        agent_id = (agent_id or "main").strip() or "main"
        if self.registry is not None and agent_id not in self.registry.list_ids():
            log.warning("webhook task: unknown agent '%s' — ignoring", agent_id)
            return
        session_key = f"agent:{agent_id}:hook:{uuid.uuid4().hex[:8]}"
        handle = RunHandle(run_id=uuid.uuid4().hex[:12], session_key=session_key,
                           abort=asyncio.Event(), client_id=None)
        await self._run(handle, task, mode=RunMode.INTERACTIVE, agent_id=agent_id)

    def _build_create_webhook(self) -> None:
        """Register create_webhook (D) when enabled — the agent mints a /hook/<id> URL by chatting.
        Gated by webhook_workshop. Needs the webhook server (started above) to add hooks live."""
        if not getattr(self.config, "webhook_workshop", False):
            return
        from agentd.infrastructure.tools.create_webhook_tool import CreateWebhookTool
        from agentd.infrastructure.tools.guard import GuardedTool, resolve_policy

        tool = CreateWebhookTool(self._create_webhook)
        self.service.add_tools([GuardedTool(tool, resolve_policy(self.config, tool))])
        log.info("create_webhook enabled (agent can mint webhook triggers)")

    async def _create_webhook(self, params: dict) -> dict:
        """Mint a task hook: a random id+secret bound to an agent, registered LIVE on the webhook
        server and persisted. Returns the URL + secret to paste into the external service."""
        import re
        import secrets
        if self.webhook_server is None:
            return {"created": False, "error": "webhook server not running"}
        agent = (params.get("agent") or "main").strip() or "main"
        if self.registry is not None and agent not in self.registry.list_ids():
            return {"created": False, "error": f"unknown agent: {agent}"}
        hid = re.sub(r"[^a-z0-9-]+", "-", (params.get("id") or "").strip().lower()).strip("-") \
            or f"hook-{secrets.token_hex(3)}"
        if hid in {h.get("id") for h in (self.config.webhooks or [])}:
            return {"created": False, "error": f"a hook '{hid}' already exists"}
        hook = {"id": hid, "secret": secrets.token_urlsafe(24), "agent": agent}
        task = (params.get("task") or "").strip()
        if task:
            hook["task"] = task
        self.webhook_server.add_hook(hook)
        self.config.webhooks = list(self.config.webhooks or []) + [hook]
        persisted = _persist_webhooks(self.config)
        base = (getattr(self.config, "public_url", "")
                or f"http://{getattr(self.config, 'webhook_host', '0.0.0.0')}:"
                   f"{getattr(self.config, 'webhook_port', 8788)}").rstrip("/")
        log.info("create_webhook '%s' -> agent '%s', persisted=%s", hid, agent, persisted)
        return {"created": True, "id": hid, "secret": hook["secret"],
                "url": f"{base}/hook/{hid}", "agent": agent, "persisted": persisted}

    async def _invoke_tool(self, name: str, params: dict) -> str:
        """Invoke a registered (namespaced MCP) tool by name OUTSIDE the agent loop —
        lets a channel send/poll via an MCP (e.g. Gmail). Returns the tool's text."""
        tool = self.service.find_tool(name)
        if tool is None:
            raise RuntimeError(f"tool not available: {name}")
        result = await tool.execute(uuid.uuid4().hex[:8], params or {}, asyncio.Event())
        text = "".join(getattr(b, "text", "") for b in (result.content or []))
        if result.is_error:
            raise RuntimeError(text or f"{name} failed")
        return text

    async def _fire_channel(self, channel, msg) -> None:
        """An inbound message arrived -> run the bound agent on a conversation-bound
        session and reply on the SAME channel. Busy-guarded per peer."""
        agent_id = getattr(channel, "agent_id", "main")
        session_key = f"agent:{agent_id}:{channel.name}:{msg.peer}"
        existing = self.runs.get(session_key)
        if existing is not None and existing.task is not None and not existing.task.done():
            return  # a run for this peer is already in flight; next poll picks it up
        handle = RunHandle(run_id=uuid.uuid4().hex[:12], session_key=session_key,
                           abort=asyncio.Event(), client_id=None)
        handle.task = asyncio.create_task(self._run_channel(handle, channel, msg, agent_id))
        self.runs[session_key] = handle
        log.info("channel %s: message from %s -> run %s", channel.name, msg.peer, handle.run_id)

    async def _run_channel(self, handle: "RunHandle", channel, msg, agent_id: str) -> None:
        await self._run(handle, msg.text, mode=RunMode.CHANNEL, agent_id=agent_id)
        reply = self._last_answer(agent_id, handle.session_key)
        if reply:
            # EGRESS PRIVACY GATE: if this agent is tagged `audience = "external"` in its toml, an
            # independent judge verifies the reply is safe to send against the agent's OWN rules
            # BEFORE it leaves — blocked -> a safe replacement goes instead. Agents not tagged
            # external (and all interactive/websocket replies) are never gated.
            reply = await self._verify_safe_to_send(handle, agent_id, msg.text, reply)
            try:
                await channel.send(msg.peer, reply)
            except Exception:  # noqa: BLE001
                log.warning("channel reply send failed (%s)", channel.name, exc_info=True)

    async def _verify_safe_to_send(self, handle: "RunHandle", agent_id: str,
                                   question: str, answer: str) -> str:
        """Run the out-of-band safe-to-send gate on an outbound channel reply and return the
        text to actually send: the original answer if cleared, or a safe replacement if blocked.
        Applies ONLY to agents tagged `audience = "external"` in their toml; anything else
        (no gate built, agent unset / "internal" / other) passes through unchanged. Every
        decision is logged + recorded to the event log (audit: what was withheld and why)."""
        gate = self.safe_to_send_gate
        if gate is None:
            return answer
        spec = self._spec(agent_id)
        # Apply the gate ONLY to agents declared external-facing. Absent / "internal" / anything
        # else => not gated.
        if spec is None or spec.audience != "external":
            return answer
        from agentd.application.interfaces.safe_to_send import SafeToSendContext

        verdict = await gate.check(SafeToSendContext(
            audience=spec.audience, policy=spec.instructions or "",
            conversation=self._recent_dialog(agent_id, handle.session_key),
            question=question, answer=answer))
        if verdict.safe:
            self._emit_gate_event(handle, "allowed", "")
            return answer
        log.warning("safe-to-send: BLOCKED reply for agent %s (%s)", agent_id, verdict.reason)
        self._emit_gate_event(handle, "blocked", verdict.reason)
        return verdict.safe_reply or (
            "Sorry, I'm not able to share that here. Could you give me a few more details "
            "about your own request so I can help you directly?")

    def _spec(self, agent_id: str):
        """The AgentSpec for an id, or None (no registry / unknown id)."""
        if self.registry is None:
            return None
        try:
            return self.registry.get(agent_id)
        except KeyError:
            return None

    def _recent_dialog(self, agent_id: str, session_key: str, turns: int = 12) -> str:
        """The last `turns` user/assistant lines of this session, so the gate's judge can tell
        the recipient's OWN info (and whether they've identified themselves) from a real leak.
        Best-effort: any failure -> "" (the gate just judges with less context)."""
        try:
            from agentd.domain.messages import AssistantMessage, UserMessage
            from agentd.infrastructure.memory.local_store import SessionStore

            spec = self._spec(agent_id)
            state_dir = spec.state_dir if spec is not None else getattr(self.config, "state_dir", None)
            if state_dir is None:
                return ""
            msgs = SessionStore(state_dir, session_key).load()
            lines = []
            for m in msgs[-turns:]:
                if isinstance(m, UserMessage):
                    t = (m.content or "").strip()
                    if t:
                        lines.append(f"Customer: {t}")
                elif isinstance(m, AssistantMessage):
                    t = m.text.strip()
                    if t:
                        lines.append(f"Assistant: {t}")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 — context is a nice-to-have, never break the gate
            return ""

    def _emit_gate_event(self, handle: "RunHandle", decision: str, reason: str) -> None:
        """Record a safe-to-send decision to the durable event log (best-effort)."""
        if self.event_log is None:
            return
        try:
            self.event_log.emit(handle.session_key, handle.run_id,
                                AgentEvent("safe_to_send", {"decision": decision, "reason": reason}))
        except Exception:  # noqa: BLE001
            pass

    def _last_answer(self, agent_id: str, session_key: str) -> str:
        """The agent's last assistant text in this session — the reply to send back."""
        from agentd.domain.messages import AssistantMessage, TextContent
        from agentd.infrastructure.memory.local_store import SessionStore

        try:
            state_dir = self.config.state_dir
            if self.registry is not None:
                try:
                    state_dir = self.registry.get(agent_id).state_dir
                except KeyError:
                    pass
            for m in reversed(SessionStore(state_dir, session_key).load()):
                if isinstance(m, AssistantMessage):
                    text = "".join(c.text for c in m.content if isinstance(c, TextContent))
                    if text.strip():
                        return text.strip()
        except Exception:  # noqa: BLE001
            log.warning("could not read channel reply for %s", session_key, exc_info=True)
        return ""

    async def _post_cron(self, task) -> bool:
        """Fire a due scheduled task as a cron-mode run (the agent executes its
        payload). Returns False if the agent's cron lane is busy, so the scheduler
        leaves the task due and retries next poll (never drops a one-shot)."""
        session_key = task.session_key
        existing = self.runs.get(session_key)
        if existing is not None and existing.task is not None and not existing.task.done():
            return False
        # deliver=message -> emit the stored text verbatim; deliver=run -> execute it
        message = (OUTBOX_PROMPT.format(text=task.payload)
                   if getattr(task, "delivery", "run") == "message" else task.payload)
        run_id = uuid.uuid4().hex[:12]
        handle = RunHandle(run_id=run_id, session_key=session_key,
                           abort=asyncio.Event(), client_id=None)
        if self.task_store is not None:
            handle.cron_run_id = self.task_store.record_run(task.id, task.agent_id)  # history
            handle.cron_task_id = task.id
            handle.cron_failure_alert = getattr(task, "failure_alert", 0)
        handle.task = asyncio.create_task(self._run(handle, message, mode=RunMode.CRON))
        self.runs[session_key] = handle
        log.info("cron fire: task %s -> run %s (%s)", task.id, run_id, session_key)
        return True

    async def _post_heartbeat(self, agent_id: str) -> None:
        """Turn a scheduler tick into a heartbeat run — an internal 'client' posting a
        turn for `agent:<id>:heartbeat`. Flood-guarded: skip if the previous tick for
        this agent is still running."""
        session_key = f"agent:{agent_id}:heartbeat"
        existing = self.runs.get(session_key)
        if existing is not None and existing.task is not None and not existing.task.done():
            return  # previous tick still running
        run_id = uuid.uuid4().hex[:12]
        handle = RunHandle(run_id=run_id, session_key=session_key,
                           abort=asyncio.Event(), client_id=None)
        handle.task = asyncio.create_task(
            self._run(handle, HEARTBEAT_PROMPT, mode=RunMode.HEARTBEAT))
        self.runs[session_key] = handle
        log.info("heartbeat tick: agent %s (run %s)", agent_id, run_id)

    async def _discover_mcp_tools(self) -> None:
        """Connect to configured MCP servers and add their tools to the toolset,
        each wrapped in GuardedTool like every other tool. Best-effort: a failed
        connection is logged and never blocks the gateway from serving."""
        if self.mcp_provider is None:
            return
        try:
            from agentd.domain.agent import apply_enablement

            raw = await self.mcp_provider.discover()
            # apply the SAME global on/off as the rest of the catalog (uniform layer-2 enablement)
            raw = apply_enablement(raw, getattr(self.config, "tools_enabled", None),
                                   getattr(self.config, "tools_disabled", ()))
            self.service.add_tools(_guarded_with_source(raw, self.config))
            if raw:
                log.info("MCP: added %d tool(s) to the toolset", len(raw))
        except Exception as e:  # noqa: BLE001 — MCP must never block serving
            log.warning("MCP discovery failed: %s", e)

    def _authorized(self, ws: ServerConnection) -> bool:
        """M2 auth: the client's token — `?token=` on the URL (the only slot browser
        WebSockets have) or an `Authorization: Bearer` header — must match ours."""
        if not self.auth_token:
            return True
        request = getattr(ws, "request", None)
        presented = ""
        if request is not None:
            query = parse_qs(urlsplit(getattr(request, "path", "") or "").query)
            presented = (query.get("token") or [""])[0]
            if not presented:
                auth_header = (request.headers.get("Authorization") or "")
                if auth_header.startswith("Bearer "):
                    presented = auth_header[len("Bearer "):].strip()
        return hmac.compare_digest(presented, self.auth_token)

    async def _handle_conn(self, ws: ServerConnection) -> None:
        if not self._authorized(ws):
            await ws.close(code=4401, reason="unauthorized")
            return
        # Each connection — terminal, desktop, mobile, a channel adapter, anything —
        # gets a stable client id. Runs it starts are tagged with it, so when this
        # connection drops we can stop exactly that client's in-flight work.
        client_id = uuid.uuid4().hex
        self.clients.add(ws)
        try:
            async for raw in ws:
                try:
                    frame = parse_frame(raw)
                except ProtocolError as e:
                    await ws.send(dump_frame(Response(id="", ok=False, payload={"error": str(e)})))
                    continue
                if isinstance(frame, Request):
                    response = await self._dispatch(frame, client_id)
                    await ws.send(dump_frame(response))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)
            await self._abort_client_runs(client_id)

    # --------------------------------------------------------------- dispatch

    async def _dispatch(self, req: Request, client_id: str | None = None) -> Response:
        try:
            if req.method == "chat.send":
                payload = await self._chat_send(req.params, client_id)
            elif req.method == "chat.abort":
                payload = await self._chat_abort(req.params)
            elif req.method == "hello":
                payload = self._hello()
            elif req.method == "sessions.list":
                payload = self._sessions_list(req.params)
            elif req.method == "sessions.history":
                payload = self._sessions_history(req.params)
            elif req.method == "sessions.rename":
                payload = await self._sessions_rename(req.params)
            elif req.method == "sessions.delete":
                payload = await self._sessions_delete(req.params)
            elif req.method == "projects.list":
                payload = self._projects_list()
            elif req.method == "projects.create":
                payload = await self._projects_create(req.params)
            elif req.method == "projects.rename":
                payload = await self._projects_rename(req.params)
            elif req.method == "projects.delete":
                payload = await self._projects_delete(req.params)
            elif req.method == "agents.list":
                payload = self._agents_list()
            elif req.method == "tools.list":
                payload = self._tools_list(req.params)
            elif req.method == "mcp.add":
                payload = await self._mcp_add(req.params)
            elif req.method == "mcp.list":
                payload = self._mcp_list()
            elif req.method == "mcp.remove":
                payload = self._mcp_remove(req.params)
            elif req.method == "agents.create":
                payload = await self._agents_create(req.params)
            elif req.method == "agents.remove":
                payload = self._agents_remove(req.params)
            elif req.method == "cron.list":
                payload = self._cron_list()
            elif req.method == "cron.add":
                payload = self._cron_add(req.params)
            elif req.method == "cron.update":
                payload = self._cron_update(req.params)
            elif req.method == "cron.remove":
                payload = self._cron_remove(req.params)
            elif req.method == "cron.run":
                payload = self._cron_run(req.params)
            elif req.method == "cron.runs":
                payload = self._cron_runs(req.params)
            elif req.method == "notifications.list":
                payload = self._notifications_list(req.params)
            elif req.method == "notifications.ack":
                payload = self._notifications_ack(req.params)
            elif req.method == "workspace.cleanup":
                payload = self._workspace_cleanup(req.params)
            elif req.method == "marketplace.catalog":
                payload = await self._marketplace().catalog()
            elif req.method == "marketplace.installed":
                payload = self._marketplace().installed()
            elif req.method == "marketplace.install":
                payload = await self._marketplace().install(
                    bundle_id=(req.params.get("id") or "").strip(),
                    file=(req.params.get("file") or "").strip())
            elif req.method == "marketplace.uninstall":
                payload = await self._marketplace().uninstall(
                    (req.params.get("id") or "").strip(),
                    purge_state=bool(req.params.get("purge")))
            else:
                return Response(id=req.id, ok=False, payload={"error": f"unknown method: {req.method}"})
            return Response(id=req.id, ok=True, payload=payload)
        except Exception as e:
            log.exception("dispatch error for %s", req.method)
            return Response(id=req.id, ok=False, payload={"error": f"{type(e).__name__}: {e}"})

    def _resolve_state_dir(self, agent_id: str) -> tuple[str, object]:
        """(effective agent id, its state_dir). Each agent partitions its own transcripts;
        an unknown id falls back to the default agent. The one place session RPCs map an
        agent to where its threads live."""
        agent_id = (agent_id or "").strip() or "main"
        if self.registry is None:
            return agent_id, self.config.state_dir
        try:
            return agent_id, self.registry.get(agent_id).state_dir
        except KeyError:                           # unknown id -> the default agent
            return "main", self.registry.get("main").state_dir

    def _sessions_list(self, params: dict) -> dict:
        """Saved sessions for ONE agent (with display titles). Each agent partitions its
        own transcripts, so resuming is agent-scoped: a client passes the agent it's on
        and gets THAT agent's threads. Defaults to the default agent when none is given."""
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        return {"sessions": list_sessions(state_dir), "agentId": agent_id}

    def _sessions_history(self, params: dict) -> dict:
        """One saved session's full transcript (messages in wire form) so a client can
        RENDER a resumed conversation — the read side of `sessions.list`. Agent-scoped;
        read-only (never creates a session). The client transforms it into its own view."""
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        session_key = (params.get("sessionKey") or params.get("sessionId") or "").strip()
        if not session_key:
            return {"messages": [], "sessionKey": "", "agentId": agent_id}
        from agentd.infrastructure.memory.local_store import read_session_messages
        messages = [_trim_history_message(m) for m in read_session_messages(state_dir, session_key)]
        return {"messages": messages, "sessionKey": session_key, "agentId": agent_id}

    async def _sessions_rename(self, params: dict) -> dict:
        """Set a session's display title (a user rename — `manual`, so auto-titling never
        overwrites it). Agent-scoped; broadcasts sessions.changed so every client's list
        updates live. An empty title clears the manual name -> falls back to auto/snippet."""
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        session_key = (params.get("sessionKey") or params.get("sessionId") or "").strip()
        if not session_key:
            return {"ok": False, "error": "sessionKey required"}
        from agentd.infrastructure.memory.local_store import write_session_meta
        title = (params.get("title") or "").strip()[:80]
        write_session_meta(state_dir, session_key, title=title, manual=bool(title))
        await self._send_all(dump_frame(Event(
            event="sessions.changed", payload={"agentId": agent_id, "sessionKey": session_key})))
        return {"ok": True, "sessionKey": session_key, "title": title, "agentId": agent_id}

    async def _sessions_delete(self, params: dict) -> dict:
        """Delete a saved conversation (transcript + meta) — any client, same backend.
        Refuses while the session has an in-flight run (abort it first); broadcasts
        sessions.changed so every connected client's list updates live."""
        agent_id, state_dir = self._resolve_state_dir(params.get("agentId"))
        session_key = (params.get("sessionKey") or params.get("sessionId") or "").strip()
        if not session_key:
            return {"ok": False, "error": "sessionKey required"}
        handle = self.runs.get(session_key)
        if handle is not None and handle.task is not None and not handle.task.done():
            return {"ok": False, "error": "session has an active run — /abort it first"}
        from agentd.infrastructure.memory.local_store import delete_session
        deleted = delete_session(state_dir, session_key)
        self.runs.pop(session_key, None)               # forget any finished handle
        await self._send_all(dump_frame(Event(
            event="sessions.changed", payload={"agentId": agent_id, "sessionKey": session_key,
                                               "deleted": True})))
        return {"ok": True, "deleted": deleted, "sessionKey": session_key, "agentId": agent_id}

    # ------------------------------------------------------------------ projects
    # Projects are SERVER data (one global list in the daemon's root state dir) so
    # every client shows the same folders; a session joins one via its meta sidecar.

    def _all_state_dirs(self) -> list:
        """Every place session transcripts live: the default state dir + each agent's
        partition — for project-wide session operations."""
        dirs = {str(self.config.state_dir): self.config.state_dir}
        if self.registry is not None:
            for aid in self.registry.list_ids():
                try:
                    sd = self.registry.get(aid).state_dir
                    dirs[str(sd)] = sd
                except KeyError:
                    continue
        return list(dirs.values())

    def _projects_list(self) -> dict:
        from agentd.infrastructure.memory import projects_store
        return {"projects": projects_store.list_projects(self.config.state_dir)}

    async def _projects_create(self, params: dict) -> dict:
        from agentd.infrastructure.memory import projects_store
        project = projects_store.create_project(self.config.state_dir,
                                                str(params.get("name") or ""))
        await self._send_all(dump_frame(Event(event="projects.changed", payload={})))
        return {"ok": True, "project": project}

    async def _projects_rename(self, params: dict) -> dict:
        from agentd.infrastructure.memory import projects_store
        ok = projects_store.rename_project(self.config.state_dir,
                                           (params.get("id") or "").strip(),
                                           str(params.get("name") or ""))
        if ok:
            await self._send_all(dump_frame(Event(event="projects.changed", payload={})))
        return {"ok": ok}

    async def _projects_delete(self, params: dict) -> dict:
        """Delete a project. Its chats become standalone by default; pass
        deleteSessions=true to remove them too (across every agent's partition)."""
        from agentd.infrastructure.memory import projects_store
        from agentd.infrastructure.memory.local_store import (
            delete_session,
            sessions_in_project,
            write_session_meta,
        )
        project_id = (params.get("id") or "").strip()
        if not project_id:
            return {"ok": False, "error": "id required"}
        removed = projects_store.delete_project(self.config.state_dir, project_id)
        sessions_deleted = 0
        for state_dir in self._all_state_dirs():
            for sid in sessions_in_project(state_dir, project_id):
                if params.get("deleteSessions"):
                    delete_session(state_dir, sid)
                    sessions_deleted += 1
                else:                                   # untag -> standalone chat
                    write_session_meta(state_dir, sid, projectId="")
        await self._send_all(dump_frame(Event(event="projects.changed", payload={})))
        await self._send_all(dump_frame(Event(event="sessions.changed", payload={})))
        return {"ok": removed, "sessionsDeleted": sessions_deleted}

    async def _maybe_generate_title(self, session_key: str, agent_id: str | None) -> None:
        """After a session's first interactive exchange, generate a short title (once) and
        store it — LM-Studio style. Skips if a title already exists (auto or user). Runs as
        a background task off the run's finally; best-effort, never affects the run."""
        try:
            from agentd.application.tool_models import brain_model, resolve_tool_model
            from agentd.infrastructure.memory.local_store import (
                read_session_messages,
                read_session_meta,
                write_session_meta,
            )
            from agentd.infrastructure.session_titles import generate_title

            aid = (agent_id or "").strip()
            if not aid and self.registry is not None:
                try:
                    aid = self.registry.resolve(session_key).id
                except Exception:  # noqa: BLE001
                    aid = "main"
            aid, state_dir = self._resolve_state_dir(aid)
            if read_session_meta(state_dir, session_key).get("title"):
                return                                 # already titled — do it once
            messages = read_session_messages(state_dir, session_key)
            first_user = next((m["content"] for m in messages if m.get("role") == "user"), "")
            if not first_user:
                return
            first_assistant = ""
            for m in messages:
                if m.get("role") == "assistant":
                    first_assistant = "".join(b.get("text", "") for b in m.get("content", [])
                                              if b.get("type") == "text")
                    if first_assistant:
                        break
            # title model: a config override (plugins.titles.tools.generate.model), else the
            # cheap cost-efficiency text model if set, else the agent's brain. Small call.
            ce = getattr(self.config, "cost_efficiency", None) or {}
            default_model = ce.get("text_model") or brain_model(self.config)
            model = resolve_tool_model(self.config, "titles", "generate", default=default_model)
            title = await asyncio.to_thread(generate_title, first_user, first_assistant, model)
            if title:
                write_session_meta(state_dir, session_key, title=title, auto=True)
                await self._send_all(dump_frame(Event(
                    event="sessions.changed", payload={"agentId": aid, "sessionKey": session_key})))
                log.info("session '%s' titled: %r", session_key, title)
        except Exception:  # noqa: BLE001 — titling must never break anything
            log.debug("auto-title failed for %s", session_key, exc_info=True)

    async def _mcp_add(self, params: dict) -> dict:
        """Hot-add an MCP server: build the config, connect it LIVE, merge its tools into the
        catalog (no restart), and PERSIST to config.mcp_servers (writes agentd.config.json). The
        central registry for bare connections — `claude mcp add` / `openclaw mcp add` equivalent."""
        from agentd.config import McpServerConfig
        from agentd.domain.agent import apply_enablement

        name = (params.get("name") or "").strip()
        command = params.get("command") or None
        url = (params.get("url") or "").strip() or None
        if not name:
            return {"added": False, "error": "name required"}
        if not (command or url):
            return {"added": False, "error": "need a command (stdio) or url (http)"}
        if any(getattr(s, "name", "") == name for s in (self.config.mcp_servers or [])):
            return {"added": False, "error": f"server '{name}' already exists"}
        cfg = McpServerConfig(name=name, transport="http" if url else "stdio",
                              command=command, url=url, env=params.get("env") or None,
                              headers=params.get("headers") or None)
        provider = self._ensure_mcp_provider()
        if provider is None:
            return {"added": False, "error": "MCP SDK not installed (pip install mcp)"}
        tools = await provider.add_server(cfg)
        if not tools:
            return {"added": False, "error": f"could not connect to '{name}'"}
        tools = apply_enablement(tools, getattr(self.config, "tools_enabled", None),
                                 getattr(self.config, "tools_disabled", ()))
        self.service.add_tools(_guarded_with_source(tools, self.config))
        self.config.mcp_servers = list(self.config.mcp_servers or []) + [cfg]
        persisted = _persist_mcp_servers(self.config)
        log.info("mcp.add '%s' -> %d tool(s), persisted=%s", name, len(tools), persisted)
        return {"added": True, "name": name, "tools": [getattr(t, "name", "") for t in tools],
                "persisted": persisted}

    def _ensure_mcp_provider(self):
        """The live MCP provider, building an empty one on first hot-add if none exists yet
        (no servers were configured at startup). None if the `mcp` SDK isn't installed."""
        if self.mcp_provider is not None:
            return self.mcp_provider
        try:
            import mcp  # noqa: F401
            from agentd.infrastructure.tools.mcp.provider import McpProvider
            from agentd.infrastructure.tools.mcp.session import create_session
            self.mcp_provider = McpProvider([], create_session)
            return self.mcp_provider
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------- marketplace (M4)

    def _marketplace(self):
        """Lazy marketplace service: progress broadcasts to every client (the store UI
        renders them) and after_change hot-reloads agents + plugins — install to usable
        with NO restart."""
        if self.marketplace is None:
            from agentd.infrastructure.marketplace import build_marketplace_service

            self.marketplace = build_marketplace_service(
                self.config, on_event=self._marketplace_progress,
                after_change=self._marketplace_after_change)
        return self.marketplace

    def _marketplace_progress(self, payload: dict) -> None:
        """Sync -> async bridge for install progress (the service is transport-blind)."""
        try:
            asyncio.get_running_loop().create_task(
                self._send_all(dump_frame(Event(event="marketplace.progress", payload=payload))))
        except RuntimeError:   # no loop (CLI offline path) — progress goes nowhere, fine
            pass

    def _marketplace_after_change(self, changed: dict | None = None) -> dict:
        """Post-install/uninstall: re-scan agents, hot-load any NEW plugins' tools, and
        tell every client the agent list changed (switchers refresh live)."""
        # An acquired addon JOINS the provisioning set (tiers doc §3) — extend the
        # in-memory profile BEFORE re-discovery, or a Studio flavor would gate out the
        # plugins it just installed. (load_config unions the ledger on every start.)
        new_plugins = tuple((changed or {}).get("plugins") or ())
        profile = getattr(self.config, "distribution", None)
        if new_plugins and profile is not None and profile.provisioned_plugins is not None:
            import dataclasses

            merged = tuple(dict.fromkeys(profile.provisioned_plugins + new_plugins))
            self.config.distribution = dataclasses.replace(profile, provisioned_plugins=merged)
        agents: list = []
        if self.registry is not None and hasattr(self.registry, "refresh"):
            agents = self.registry.refresh()
        tools: list = []
        reloader = getattr(self.service, "plugin_reloader", None)
        if callable(reloader):
            tools = (reloader() or {}).get("tools", [])
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send_all(dump_frame(Event(
                event="agents.changed", payload=self._agents_list()))))
            # a freshly installed agent gets its tagline/suggestions generated too
            loop.create_task(self._maybe_generate_presentations())
        except RuntimeError:
            pass
        return {"agents": agents, "tools": tools}

    def _mcp_list(self) -> dict:
        """Every MCP server — config-registered AND plugin-MCP — with whether its tools are live.
        The unified 'what MCP do I have' surface (matches /tools for tools)."""
        loaded = {info["name"].split("__", 1)[0]
                  for info in self.service.list_tools() if "__" in info.get("name", "")}
        servers = [{
            "name": getattr(s, "name", ""), "transport": getattr(s, "transport", "stdio"),
            "command": getattr(s, "command", None), "url": getattr(s, "url", None),
            "connected": getattr(s, "name", "") in loaded,
        } for s in (self.config.mcp_servers or [])]
        return {"servers": servers, "count": len(servers)}

    def _mcp_remove(self, params: dict) -> dict:
        """Remove an MCP server: drop it from config (persisted) + drop its live tools."""
        name = (params.get("name") or "").strip()
        if not name:
            return {"removed": False, "error": "name required"}
        servers = [s for s in (self.config.mcp_servers or []) if getattr(s, "name", "") != name]
        if len(servers) == len(self.config.mcp_servers or []):
            return {"removed": False, "error": f"no such server: {name}"}
        self.config.mcp_servers = servers
        dropped = self.service.remove_tools(f"{name}__")
        _persist_mcp_servers(self.config)
        return {"removed": True, "name": name, "toolsDropped": dropped}

    def _tools_list(self, params: dict) -> dict:
        """The live tool catalog — the uniform 'what tools exist' surface any client can render.
        No agentId => the full active catalog; with agentId => the subset THAT agent sees in an
        interactive turn (its allow/deny scope), i.e. what the model would be handed."""
        agent_id = (params.get("agentId") or "").strip() or None
        tools = self.service.list_tools(agent_id)
        return {"tools": tools, "count": len(tools), "agentId": agent_id}

    def _agents_list(self) -> dict:
        """The available agents — the uniform discovery surface any client uses. The
        registry is the single source of truth; the session-key format stays internal.
        Includes each agent's display presentation (tagline + starter suggestions) so
        no client ever hardcodes what an agent 'is'."""
        default = getattr(self.config, "agent_id", "main")
        if self.registry is None:
            return {"agents": [{"id": default, "name": self.config.agent_name}], "default": default}
        agents = []
        for aid in self.registry.list_ids():
            spec = self.registry.get(aid)
            agents.append({
                "id": aid, "name": spec.name,
                "version": getattr(spec, "version", "1"),
                "tagline": getattr(spec, "tagline", ""),
                "suggestions": list(getattr(spec, "suggestions", ()) or ()),
                "color": getattr(spec, "color", ""),
            })
        return {"agents": agents, "default": default if default in {a["id"] for a in agents} else "main"}

    async def _maybe_generate_presentations(self) -> None:
        """Fill in missing agent display presentation, persisted per agent in a sidecar
        (authored agent.toml fields always win), then agents.changed tells every client.
        Two independent concerns, both best-effort and never blocking:
          1. COLOUR — pure/cheap, assigned to EVERY agent (unique across the set, so it
             needs the whole registry; that's why it's server-side, not per-client).
          2. TAGLINE + suggestions — one LLM call over the agent's identity, only for
             agents that actually have an identity to describe."""
        if self.registry is None:
            return
        try:
            from agentd.infrastructure.agents import presentation as pres

            changed = False
            # --- 1. colours: unique, unconditional (even identity-less agents) ---------
            taken: list[float] = []
            missing: list = []
            for aid in self.registry.list_ids():
                try:
                    spec = self.registry.get(aid)
                except KeyError:
                    continue
                if getattr(spec, "dir", None) is None:
                    continue                     # nowhere to persist (synthesized main)
                if getattr(spec, "color", ""):
                    hue = pres.hex_to_hue(spec.color)
                    if hue is not None:
                        taken.append(hue)        # an assigned/authored colour is 'taken'
                else:
                    missing.append(spec)
            for spec in missing:
                hue = pres.assign_hue(spec.id, taken)
                taken.append(hue)
                pres.update_sidecar(spec.dir, color=pres.hsl_to_hex(hue), hue=round(hue, 1))
                changed = True
                log.info("agent '%s' coloured: %s", spec.id, pres.hsl_to_hex(hue))

            # --- 2. taglines + suggestions: LLM, identity-bearing agents only ----------
            from agentd.application.tool_models import brain_model, resolve_tool_model

            for aid in self.registry.list_ids():
                try:
                    spec = self.registry.get(aid)
                except KeyError:
                    continue
                if getattr(spec, "tagline", "") or getattr(spec, "dir", None) is None:
                    continue
                ce = getattr(self.config, "cost_efficiency", None) or {}
                default_model = ce.get("text_model") or brain_model(self.config)
                model = resolve_tool_model(self.config, "agents", "presentation",
                                           default=default_model)
                data = await asyncio.to_thread(
                    pres.generate_presentation, spec.name,
                    getattr(spec, "description", ""), spec.instructions, model)
                if not data:
                    continue
                pres.update_sidecar(spec.dir, **data)
                changed = True
                log.info("agent '%s' presented: %r", aid, data.get("tagline"))

            if changed:
                self.registry.refresh()          # sidecars -> live specs
                await self._send_all(dump_frame(Event(
                    event="agents.changed", payload=self._agents_list())))
        except Exception:  # noqa: BLE001 — presentation is décor, never breaks serving
            log.debug("agent presentation generation failed", exc_info=True)

    def _agents_remove(self, params: dict) -> dict:
        """Permanently delete an agent EVERYWHERE — the one destructive surface any client
        uses. Purges the shared ledgers (memory + cron/goals/runs/notifs/commitments) first
        so nothing can fire orphaned, then deletes the definition + workspace + sessions and
        forgets it (no restart). Refuses 'main'."""
        agent_id = (params.get("agentId") or "").strip().lower()
        if not agent_id:
            return {"removed": False, "error": "agentId required"}
        if agent_id == "main":
            return {"removed": False, "error": "cannot delete the default agent 'main'"}
        if self.registry is None:
            return {"removed": False, "error": "no agent registry"}
        if agent_id not in self.registry.list_ids():
            return {"removed": False, "error": f"unknown agent: {agent_id}"}

        cron = self.task_store.purge_agent(agent_id) if self.task_store is not None else {}
        memory = self.memory_bank.purge_agent(agent_id) if self.memory_bank is not None else 0
        removed = self.registry.remove(agent_id)   # definition + workspace + sessions, drop from cache
        log.info("agents.remove %s -> %s cron=%s memory=%s", agent_id, removed, cron, memory)
        return {"removed": True, "agentId": agent_id,
                "definition": removed.get("definition", False),
                "sessions": removed.get("sessions", False),
                "cron": cron, "memory": memory}

    async def _agents_create(self, params: dict) -> dict:
        """Create a new agent from a client (the 'Create agent' button) — the uniform
        authoring surface. Scaffolds agents/<id>/ (agent.toml + optional IDENTITY.md),
        loads it live (no restart), then kicks the presentation pass to give it a unique
        colour + tagline. Broadcasts agents.changed so every client shows it immediately."""
        if self.registry is None or not hasattr(self.registry, "create"):
            return {"created": False, "error": "no agent registry"}
        agent_id = (params.get("agentId") or params.get("id") or "").strip().lower()
        name = str(params.get("name") or "").strip()
        if not agent_id:
            # derive a slug from the name when the client didn't supply an id
            agent_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not agent_id:
            return {"created": False, "error": "name or id required"}
        try:
            spec = self.registry.create(
                agent_id, name=name,
                description=str(params.get("description") or "").strip(),
                identity=str(params.get("identity") or params.get("instructions") or "").strip())
        except ValueError as e:
            return {"created": False, "error": str(e)}
        # show it right away, then fill colour/tagline in the background
        await self._send_all(dump_frame(Event(event="agents.changed", payload=self._agents_list())))
        asyncio.create_task(self._maybe_generate_presentations())
        log.info("agents.create %s (%s)", spec.id, name or spec.id)
        return {"created": True, "agentId": spec.id, "name": spec.name}

    def _workspace_cleanup(self, params: dict) -> dict:
        """Tidy an agent's workspace: delete scratch (all of <workspace>/tmp/) + any file
        matching the given glob patterns. Dry-run by DEFAULT (returns what WOULD be deleted);
        apply=true actually deletes. Stale index rows auto-prune on the agent's next turn."""
        from agentd.infrastructure.workspace.cleanup import cleanup, plan_cleanup
        agent_id = (params.get("agentId") or "main").strip().lower()
        patterns = tuple(params.get("patterns") or ())
        apply = bool(params.get("apply"))
        if self.registry is None:
            return {"error": "no agent registry"}
        try:
            ws = self.registry.get(agent_id).workspace
        except KeyError:
            return {"error": f"no such agent: {agent_id}"}
        if apply:
            deleted = cleanup(ws, patterns=patterns)
            log.info("workspace.cleanup %s -> deleted %d", agent_id, len(deleted))
            return {"agentId": agent_id, "applied": True, "deleted": deleted, "count": len(deleted)}
        targets = plan_cleanup(ws, patterns=patterns)
        return {"agentId": agent_id, "applied": False, "wouldDelete": targets, "count": len(targets)}

    def _cron_list(self) -> dict:
        """Scheduled jobs across ALL agents + recent runs — the uniform 'list my jobs'
        surface any client can render (same source of truth as the cron tool)."""
        if self.task_store is None:
            return {"autonomy": False, "jobs": [], "runs": []}

        def sched(t) -> str:
            if t.kind == "cron":
                return f"cron '{t.cron_expr}'" + (f" {t.tz}" if t.tz else "")
            if t.kind == "every":
                return f"every {int(t.every_seconds)}s"
            return "once"

        jobs = [{
            "id": t.id, "agentId": t.agent_id, "kind": t.kind, "schedule": sched(t),
            "nextDue": datetime.fromtimestamp(t.next_due).strftime("%Y-%m-%d %H:%M"),
            "enabled": bool(t.enabled), "delivery": t.delivery, "payload": t.payload,
        } for t in self.task_store.list(None)]
        runs = [{
            "taskId": r.task_id, "agentId": r.agent_id, "status": r.status,
            "detail": r.detail,
            "at": datetime.fromtimestamp(r.started_at).strftime("%Y-%m-%d %H:%M"),
        } for r in self.task_store.recent_runs(limit=10)]
        return {"autonomy": True, "jobs": jobs, "runs": runs}

    def _cron_runs(self, params: dict) -> dict:
        """Full run history — the uniform 'view history' surface any client can render.
        Optional id (one job) / agentId filter; limit (default 200)."""
        if self.task_store is None:
            return {"autonomy": False, "runs": []}
        tid = (params.get("id") or "").strip() or None
        aid = (params.get("agentId") or "").strip() or None
        try:
            limit = max(1, min(int(params.get("limit", 200)), 1000))
        except (TypeError, ValueError):
            limit = 200
        runs = [{
            "id": r.id, "taskId": r.task_id, "agentId": r.agent_id, "status": r.status,
            "outcome": r.outcome, "detail": r.detail,
            "startedAt": datetime.fromtimestamp(r.started_at).strftime("%Y-%m-%d %H:%M:%S"),
            "finishedAt": (datetime.fromtimestamp(r.finished_at).strftime("%H:%M:%S")
                           if r.finished_at else None),
            "durationSec": (round(r.finished_at - r.started_at, 1) if r.finished_at else None),
        } for r in self.task_store.recent_runs(agent_id=aid, task_id=tid, limit=limit)]
        return {"autonomy": True, "runs": runs}

    def _require_store(self):
        if self.task_store is None:
            raise RuntimeError("autonomy is off — set AGENTD_AUTONOMY=1 and restart the gateway")
        return self.task_store

    def _cron_add(self, params: dict) -> dict:
        """Create a job for an agent (client-driven; mirrors the cron tool's 'add')."""
        from agentd.infrastructure.autonomy.schedule import resolve_schedule

        store = self._require_store()
        payload = (params.get("payload") or "").strip()
        if not payload:
            raise ValueError("payload is required")
        agent_id = (params.get("agentId") or "main").strip() or "main"
        if self.registry is not None and agent_id not in self.registry.list_ids():
            raise ValueError(f"unknown agent: {agent_id}")
        deliver = (params.get("deliver") or "run").strip()
        tid = uuid.uuid4().hex[:12]
        task = ScheduledTask(
            id=tid, agent_id=agent_id, session_key=cron_session_key(agent_id, tid),
            payload=payload, enabled=True, created_at=time.time(),
            delivery=deliver if deliver in ("run", "message") else "run",
            **resolve_schedule(params))
        store.add(task)
        return {"id": task.id}

    def _cron_update(self, params: dict) -> dict:
        from agentd.infrastructure.autonomy.schedule import resolve_schedule

        store = self._require_store()
        tid = (params.get("id") or "").strip()
        if not tid or store.get(tid) is None:
            raise ValueError(f"no such job: {tid}")
        fields: dict = {}
        if any(params.get(k) for k in ("cron", "daily", "every", "in", "at")):
            fields.update(resolve_schedule(params))
            fields["enabled"] = 1
        if params.get("payload"):
            fields["payload"] = params["payload"].strip()
        if params.get("deliver") in ("run", "message"):
            fields["delivery"] = params["deliver"]
        if "enabled" in params:
            fields["enabled"] = 1 if params["enabled"] else 0
        if not fields:
            raise ValueError("nothing to update")
        store.update(tid, **fields)
        return {"ok": True, "id": tid}

    def _cron_remove(self, params: dict) -> dict:
        store = self._require_store()
        tid = (params.get("id") or "").strip()
        return {"removed": bool(tid and store.remove(tid)), "id": tid}

    def _cron_run(self, params: dict) -> dict:
        store = self._require_store()
        tid = (params.get("id") or "").strip()
        if not tid or store.get(tid) is None:
            raise ValueError(f"no such job: {tid}")
        store.update(tid, next_due=time.time(), enabled=1)   # fires on the next scheduler poll
        return {"ok": True, "id": tid}

    def _hello(self) -> dict:
        """Handshake: identity + status a client renders as its welcome banner.

        The agent NAME (and all these facts) are owned by the server's config — the
        single source of truth — so every front-end shows the same thing without
        hardcoding any of it.
        """
        distribution = getattr(self.config, "distribution", None)
        return {
            "agentName": self.config.agent_name,
            "agentId": self.config.agent_id,
            "model": _effective_model(self.config),
            "reasoning": self.config.reasoning_effort,
            "gatewayUrl": f"ws://{self.config.host}:{self.config.port}",
            "workspace": str(self.config.workspace),
            "sessions": len(list_sessions(self.config.state_dir)),
            # M2 versioning: clients adapt to the daemon, never the reverse.
            "version": __version__,
            "protocol": 1,
            # M6 flavor: what THIS INSTALL is (branding + whether the store shows).
            "product": getattr(distribution, "product_name", "agentd"),
            "productId": getattr(distribution, "product_id", "agentd"),
            "storeEnabled": bool(getattr(distribution, "store_enabled", True)),
            "registryConfigured": bool(getattr(self.config, "registry_url", "")),
            "registryUrl": str(getattr(self.config, "registry_url", "") or ""),
            # where a LOCAL registry is auto-detected — clients can show real setup
            # instructions instead of a bare error (local-first store).
            "localRegistryDir": str(Path(self.config.state_dir) / "registry"),
            "agents": self._agents_list()["agents"],   # so any client can show/pick agents
        }

    async def _chat_send(self, params: dict, client_id: str | None = None) -> dict:
        session_key = params.get("sessionKey") or "default"
        message = params.get("message") or ""
        if not message.strip():
            raise ValueError("message must not be empty")

        # explicit agent selection (any client names the agent; the registry resolves
        # it — no client knows the session-key format). Unknown id -> clear error.
        agent_id = params.get("agentId") or None
        if agent_id and self.registry is not None and agent_id not in self.registry.list_ids():
            raise ValueError(f"unknown agent: {agent_id}")

        # project membership: a chat started "inside a project" carries projectId; the
        # link lives on the session's meta sidecar (server data — every client sees it).
        # Cheap guard: only write when it actually changes.
        project_id = (params.get("projectId") or "").strip()
        if project_id:
            from agentd.infrastructure.memory.local_store import (
                read_session_meta,
                write_session_meta,
            )
            _, state_dir = self._resolve_state_dir(agent_id)
            if read_session_meta(state_dir, session_key).get("projectId") != project_id:
                write_session_meta(state_dir, session_key, projectId=project_id)

        idem = params.get("idempotencyKey")
        if idem and idem in self.idempotency:
            return {"runId": self.idempotency[idem], "deduplicated": True}

        existing = self.runs.get(session_key)
        if existing is not None and existing.task is not None and not existing.task.done():
            raise RuntimeError(f"session '{session_key}' already has an active run")

        run_id = uuid.uuid4().hex[:12]
        if idem:
            self.idempotency[idem] = run_id
        handle = RunHandle(
            run_id=run_id, session_key=session_key, abort=asyncio.Event(), client_id=client_id
        )
        handle.task = asyncio.create_task(self._run(handle, message, agent_id=agent_id))
        self.runs[session_key] = handle
        return {"runId": run_id}

    def _abort_handle(self, handle: RunHandle) -> bool:
        """Signal a run to stop: set its abort flag (cooperative — the loop/tools
        check it) and cancel its task. Returns False if it wasn't running."""
        if handle.task is None or handle.task.done():
            return False
        handle.abort.set()
        handle.task.cancel()
        return True

    async def _abort_client_runs(self, client_id: str) -> None:
        """When a client connection ends, stop every in-flight run it started.

        Transport-agnostic: any front-end (terminal, desktop, mobile, a messaging
        channel adapter) that drops its connection has its own work cancelled —
        e.g. a computer-use run stops driving the PC the moment you close the app.
        Runs started by OTHER clients are untouched.
        """
        for handle in list(self.runs.values()):
            if handle.client_id == client_id and self._abort_handle(handle):
                log.info("client %s disconnected; aborting run %s (session %s)",
                         client_id, handle.run_id, handle.session_key)

    async def _chat_abort(self, params: dict) -> dict:
        session_key = params.get("sessionKey") or "default"
        handle = self.runs.get(session_key)
        if handle is None or not self._abort_handle(handle):
            return {"aborted": False, "reason": "no active run"}
        return {"aborted": True, "runId": handle.run_id}

    # -------------------------------------------------------------------- run

    async def _run(self, handle: RunHandle, message: str,
                   mode: str = RunMode.INTERACTIVE, agent_id: str | None = None) -> None:
        # The gateway (presentation) now only adapts transport: it provides the event
        # sink (broadcast) and delegates the actual work to the AgentService use-case.
        # `mode` distinguishes a normal client turn from an autonomous heartbeat tick;
        # `agent_id` is an explicit client agent selection (else resolved from the key).
        async def on_event(event: AgentEvent) -> None:
            await self._broadcast(handle.session_key, handle.run_id, event)
            # OBSERVABILITY: durably record EVERY event so a run is viewable even with no client
            # attached (cron/channel/heartbeat/sub-agent). Best-effort; never breaks the run.
            if self.event_log is not None:
                self.event_log.emit(handle.session_key, handle.run_id, event)
            # SUB-AGENT visibility: relay a compact beat to the PARENT's view so a blocked
            # parent shows its children working instead of going silent.
            if handle.parent_session_key:
                relayed = subagent_relay(handle.session_key, event)
                if relayed is not None:
                    await self._broadcast(handle.parent_session_key, handle.run_id, relayed)

        status = "ok"
        err_msg = ""
        try:
            await self.service.handle_message(
                handle.session_key, message, on_event, handle.abort,
                mode=mode, agent_id=agent_id,
            )
        except asyncio.CancelledError:
            status = "aborted"  # abort already broadcast agent_end(aborted) from the loop
        except Exception as e:
            status = "error"
            err_msg = str(e)
            log.exception("run %s crashed", handle.run_id)
            crash = AgentEvent("agent_end", {"stopReason": "error", "error": str(e)})
            await self._broadcast(handle.session_key, handle.run_id, crash)
            if self.event_log is not None:
                self.event_log.emit(handle.session_key, handle.run_id, crash)
        finally:
            # Auto-title an interactive chat after its first exchange (LM-Studio style):
            # fire-and-forget so it never delays the run; skips cron/heartbeat/aborted and
            # sessions that already have a title. Titles are conversation data (server-side),
            # so every client shows the same name.
            if mode == RunMode.INTERACTIVE and handle.cron_run_id is None and status != "aborted":
                asyncio.create_task(self._maybe_generate_title(handle.session_key, agent_id))
            # RUN seam: fold the agent's declared outcome into the headline status via the
            # pure policy. With enforce_outcome on, a cron run that finished `ok` but
            # declared nothing becomes `incomplete` (no silent success) — a decoupled
            # layer you can cut with AGENTD_ENFORCE_OUTCOME=0.
            declared = take_run_outcome()          # (raw_status, detail) | None
            status, outcome, detail = resolve_run_outcome(
                status, declared,
                enforce=getattr(self.config, "enforce_outcome", True),
                is_cron=handle.cron_run_id is not None,
            )
            if handle.cron_run_id is not None and self.task_store is not None:
                try:
                    self.task_store.finish_run(
                        handle.cron_run_id, status, outcome=outcome, detail=detail or "")
                except Exception:  # noqa: BLE001
                    pass
            # reach the user when a SCHEDULED run couldn't finish on its own (5a) —
            # gated on cron_run_id so interactive/heartbeat runs never push a notice.
            if (self.notifier is not None and handle.cron_run_id is not None
                    and status in ("blocked", "failed", "error", "incomplete")):
                await self._notify_run(handle, status, detail or err_msg)
            # failure-alert escalation (S14): after N consecutive failed/incomplete runs,
            # AUTO-PAUSE the job so a broken task stops running (+ spamming) forever. The
            # job's own failure_alert wins; else the global default (cron_failure_alert_default).
            alert = handle.cron_failure_alert or getattr(self.config, "cron_failure_alert_default", 0)
            if (alert and self.task_store is not None
                    and status in ("failed", "error", "aborted", "incomplete")
                    and self.task_store.consecutive_failures(handle.cron_task_id) >= alert):
                try:
                    self.task_store.update(handle.cron_task_id, enabled=0)
                    if self.notifier is not None:
                        await self._notify_run(
                            handle, "failed",
                            f"paused after {alert} consecutive failed/incomplete runs — "
                            f"needs your attention.")
                except Exception:  # noqa: BLE001
                    pass

    async def _broadcast(self, session_key: str, run_id: str, event: AgentEvent) -> None:
        # `ts` (epoch seconds) stamps every live event server-side, so all clients
        # show the same send time — and it matches the transcript's stored timestamps.
        await self._send_all(dump_frame(Event(
            event="chat.event",
            payload={"sessionKey": session_key, "runId": run_id, "ts": time.time(),
                     "event": event.to_dict()},
        )))

    async def _send_all(self, frame: str) -> None:
        """Send one frame to every connected client, pruning dead connections."""
        dead = []
        for ws in self.clients:
            try:
                await ws.send(frame)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    # ---------------------------------------------------------- notifications

    def _build_notifier(self) -> None:
        """Compose the outbound notify channels (client-push + durable store). Default
        on; AGENTD_NOTIFY=0 disables it. The task_store doubles as the NotifyStore."""
        if self.notifier is not None or not getattr(self.config, "notify_enabled", True):
            return
        from agentd.infrastructure.notify import build_notifier

        self.notifier = build_notifier(
            self.task_store, self._push_notification, extra=self.channel_notifiers)

    async def _push_notification(self, n: Notification) -> None:
        """Broadcast a notification to connected clients (session-less, event=notification)."""
        await self._send_all(dump_frame(Event(event="notification", payload={
            "id": n.id, "agentId": n.agent_id, "kind": n.kind,
            "text": n.text, "detail": n.detail,
            "at": datetime.fromtimestamp(n.created_at or time.time()).strftime("%Y-%m-%d %H:%M"),
        })))

    async def _notify_run(self, handle: "RunHandle", status: str, detail: str) -> None:
        """A scheduled run ended blocked/failed -> notify the user (5a)."""
        agent_id = agent_id_from_session_key(handle.session_key)
        n = Notification(
            id=uuid.uuid4().hex[:12], agent_id=agent_id, kind=status,
            text=f"{agent_id} — scheduled run {status}", detail=detail, created_at=time.time())
        try:
            await self.notifier.notify(n)
        except Exception:  # noqa: BLE001 — notify must never break the run
            log.warning("notify failed", exc_info=True)

    def _notifications_list(self, params: dict) -> dict:
        if self.task_store is None:
            return {"autonomy": False, "notifications": []}
        try:
            limit = max(1, min(int(params.get("limit", 50)), 500))
        except (TypeError, ValueError):
            limit = 50
        ns = self.task_store.notifications(
            agent_id=(params.get("agentId") or "").strip() or None,
            unread_only=bool(params.get("unread", False)), limit=limit)
        return {"autonomy": True, "notifications": [{
            "id": n.id, "agentId": n.agent_id, "kind": n.kind, "text": n.text,
            "detail": n.detail, "read": n.read,
            "at": datetime.fromtimestamp(n.created_at).strftime("%Y-%m-%d %H:%M"),
        } for n in ns]}

    def _notifications_ack(self, params: dict) -> dict:
        if self.task_store is None:
            return {"acked": 0}
        nid = (params.get("id") or "").strip()
        if nid in ("*", "all"):                     # ack everything unread
            acked = sum(1 for n in self.task_store.notifications(unread_only=True, limit=1000)
                        if self.task_store.ack(n.id))
            return {"acked": acked}
        return {"acked": int(bool(nid and self.task_store.ack(nid))), "id": nid}
