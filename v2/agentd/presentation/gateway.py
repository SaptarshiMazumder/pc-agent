"""WebSocket gateway: receives chat.send, runs the agent, broadcasts events.

Mirrors the reference gateway's chat.send semantics:
- respond immediately with {runId}; the run executes async
- one active run per session (busy -> error response)
- idempotencyKey dedupe
- loop events broadcast to all connected clients as chat.event frames
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import websockets
from websockets.asyncio.server import ServerConnection, serve

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
    clients: set[ServerConnection] = field(default_factory=set)
    runs: dict[str, RunHandle] = field(default_factory=dict)  # session_key -> handle
    idempotency: dict[str, str] = field(default_factory=dict)  # key -> run_id

    # ------------------------------------------------------------------ serve

    async def serve(self) -> None:
        await self._discover_mcp_tools()  # connect external MCP servers, add their tools
        self._build_subagents()           # the spawn_subagent tool (S8), if enabled
        self._build_channels()            # messaging channels (5b) — email needs MCP tools first
        self._build_notifier()            # outbound notifications (client-push + durable + channels)
        scheduler_task = self._start_scheduler()  # autonomy (heartbeat); None if disabled
        poller_task = self._start_channel_poller()  # inbound poll channels; None if none
        webhook_task = self._start_webhook_server()  # inbound push channels (LINE); None if none
        async with serve(self._handle_conn, self.config.host, self.config.port):
            log.info("listening on ws://%s:%s", self.config.host, self.config.port)
            print(f"agentd listening on ws://{self.config.host}:{self.config.port}")
            print(f"model: {self.config.model} | workspace: {self.config.workspace}")
            try:
                await asyncio.Future()  # run forever
            finally:
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
        log.info("sub-agents enabled (max %d concurrent)", getattr(self.config, "subagent_max", 4))

    async def _spawn_subagent(self, agent_id: str | None, task: str) -> str:
        """Run a self-contained CHILD agent turn and return its final answer. Called from
        within the parent's tool execution; the child runs as its own asyncio.Task so its
        run-context (contextvar) never clobbers the parent's. Capped + depth-limited."""
        ctx = current_run_context()
        parent_agent = (ctx.agent_id if ctx else None) or "main"
        parent_key = ctx.session_key if ctx else ""
        if ":sub:" in parent_key:                       # depth-1: a sub-agent can't spawn more
            return "sub-agents cannot themselves spawn sub-agents (depth limit)."
        cap = int(getattr(self.config, "subagent_max", 4))
        if self.subagent_active >= cap:
            return f"sub-agent limit reached ({cap} concurrent); try again when some finish."

        child_agent = (agent_id or parent_agent)
        if self.registry is not None and child_agent not in self.registry.list_ids():
            return f"unknown agent: {child_agent}"
        session_key = f"agent:{child_agent}:sub:{uuid.uuid4().hex[:8]}"
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
        """One HTTP server hosting every PUSH channel (those exposing ``webhook_path``) on
        its own path. Inbound events fire through the SAME ``_fire_channel`` path as polled
        channels, so the run/reply/notify machinery is reused. None if no push channels."""
        push = [c for c in self.channels if getattr(c, "webhook_path", None)]
        connect_on = self.credential_store is not None and self.connect_tokens is not None
        if not push and not connect_on:          # nothing needs the HTTP server
            return None
        from agentd.infrastructure.channels.webhook import WebhookServer

        server = WebhookServer(
            push, self._fire_channel,
            host=getattr(self.config, "webhook_host", "0.0.0.0"),
            port=int(getattr(self.config, "webhook_port", 8788)),
            credential_store=self.credential_store if connect_on else None,
            connect_tokens=self.connect_tokens if connect_on else None)
        return asyncio.create_task(server.run(), name="webhook-server")

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

    async def _handle_conn(self, ws: ServerConnection) -> None:
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
            else:
                return Response(id=req.id, ok=False, payload={"error": f"unknown method: {req.method}"})
            return Response(id=req.id, ok=True, payload=payload)
        except Exception as e:
            log.exception("dispatch error for %s", req.method)
            return Response(id=req.id, ok=False, payload={"error": f"{type(e).__name__}: {e}"})

    def _sessions_list(self, params: dict) -> dict:
        """Saved sessions for ONE agent. Each agent partitions its own transcripts
        (main = legacy flat path; others under agents/<id>/), so resuming is
        agent-scoped: a client passes the agent it's on and gets THAT agent's
        threads. Defaults to the default agent when no agentId is given."""
        agent_id = (params.get("agentId") or "").strip() or "main"
        state_dir = self.config.state_dir
        if self.registry is not None:
            try:
                state_dir = self.registry.get(agent_id).state_dir
            except KeyError:                       # unknown id -> fall back to default
                agent_id = "main"
                state_dir = self.registry.get("main").state_dir
        return {"sessions": list_sessions(state_dir), "agentId": agent_id}

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
        registry is the single source of truth; the session-key format stays internal."""
        default = getattr(self.config, "agent_id", "main")
        if self.registry is None:
            return {"agents": [{"id": default, "name": self.config.agent_name}], "default": default}
        agents = [{"id": aid, "name": self.registry.get(aid).name,
                   "version": getattr(self.registry.get(aid), "version", "1")}
                  for aid in self.registry.list_ids()]
        return {"agents": agents, "default": default if default in {a["id"] for a in agents} else "main"}

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
        return {
            "agentName": self.config.agent_name,
            "agentId": self.config.agent_id,
            "model": self.config.model,
            "reasoning": self.config.reasoning_effort,
            "gatewayUrl": f"ws://{self.config.host}:{self.config.port}",
            "workspace": str(self.config.workspace),
            "sessions": len(list_sessions(self.config.state_dir)),
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
        await self._send_all(dump_frame(Event(
            event="chat.event",
            payload={"sessionKey": session_key, "runId": run_id, "event": event.to_dict()},
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
