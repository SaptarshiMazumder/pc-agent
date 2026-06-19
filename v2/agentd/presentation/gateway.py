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
import uuid
from dataclasses import dataclass, field

import websockets
from websockets.asyncio.server import ServerConnection, serve

from agentd.application.services.agent_service import AgentService
from agentd.config import Config
from agentd.domain.events import AgentEvent
from agentd.infrastructure.memory.local_store import list_sessions
from agentd.presentation.protocol import Event, ProtocolError, Request, Response, dump_frame, parse_frame

log = logging.getLogger("agentd")


@dataclass
class RunHandle:
    run_id: str
    session_key: str
    abort: asyncio.Event
    client_id: str | None = None  # the client connection that started this run
    task: asyncio.Task | None = None


@dataclass
class Gateway:
    """Transport only: accepts WebSocket frames and delegates work to the injected
    ``service`` (the AgentService use-case). It is built by main/container.py — it no
    longer composes anything itself."""

    config: Config
    service: AgentService                          # injected use-case (does the work)
    browser_manager: object | None = None          # injected; closed on shutdown
    mcp_provider: object | None = None             # injected; discovered at startup, closed on shutdown
    clients: set[ServerConnection] = field(default_factory=set)
    runs: dict[str, RunHandle] = field(default_factory=dict)  # session_key -> handle
    idempotency: dict[str, str] = field(default_factory=dict)  # key -> run_id

    # ------------------------------------------------------------------ serve

    async def serve(self) -> None:
        await self._discover_mcp_tools()  # connect external MCP servers, add their tools
        async with serve(self._handle_conn, self.config.host, self.config.port):
            log.info("listening on ws://%s:%s", self.config.host, self.config.port)
            print(f"agentd listening on ws://{self.config.host}:{self.config.port}")
            print(f"model: {self.config.model} | workspace: {self.config.workspace}")
            try:
                await asyncio.Future()  # run forever
            finally:
                if self.browser_manager is not None:
                    await self.browser_manager.close()
                if self.mcp_provider is not None:
                    await self.mcp_provider.aclose()

    async def _discover_mcp_tools(self) -> None:
        """Connect to configured MCP servers and add their tools to the toolset,
        each wrapped in GuardedTool like every other tool. Best-effort: a failed
        connection is logged and never blocks the gateway from serving."""
        if self.mcp_provider is None:
            return
        try:
            from agentd.infrastructure.tools.guard import GuardedTool, resolve_policy

            raw = await self.mcp_provider.discover()
            self.service.add_tools(
                [GuardedTool(t, resolve_policy(self.config, t)) for t in raw]
            )
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
                payload = {"sessions": list_sessions(self.config.state_dir)}
            else:
                return Response(id=req.id, ok=False, payload={"error": f"unknown method: {req.method}"})
            return Response(id=req.id, ok=True, payload=payload)
        except Exception as e:
            log.exception("dispatch error for %s", req.method)
            return Response(id=req.id, ok=False, payload={"error": f"{type(e).__name__}: {e}"})

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
        }

    async def _chat_send(self, params: dict, client_id: str | None = None) -> dict:
        session_key = params.get("sessionKey") or "default"
        message = params.get("message") or ""
        if not message.strip():
            raise ValueError("message must not be empty")

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
        handle.task = asyncio.create_task(self._run(handle, message))
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

    async def _run(self, handle: RunHandle, message: str) -> None:
        # The gateway (presentation) now only adapts transport: it provides the event
        # sink (broadcast) and delegates the actual work to the AgentService use-case.
        async def on_event(event: AgentEvent) -> None:
            await self._broadcast(handle.session_key, handle.run_id, event)

        try:
            await self.service.handle_message(
                handle.session_key, message, on_event, handle.abort
            )
        except asyncio.CancelledError:
            pass  # abort already broadcast agent_end(aborted) from the loop
        except Exception as e:
            log.exception("run %s crashed", handle.run_id)
            await self._broadcast(
                handle.session_key,
                handle.run_id,
                AgentEvent("agent_end", {"stopReason": "error", "error": str(e)}),
            )

    async def _broadcast(self, session_key: str, run_id: str, event: AgentEvent) -> None:
        frame = dump_frame(
            Event(
                event="chat.event",
                payload={"sessionKey": session_key, "runId": run_id, "event": event.to_dict()},
            )
        )
        dead = []
        for ws in self.clients:
            try:
                await ws.send(frame)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
