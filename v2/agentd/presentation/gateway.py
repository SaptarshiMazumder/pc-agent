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
    task: asyncio.Task | None = None


@dataclass
class Gateway:
    """Transport only: accepts WebSocket frames and delegates work to the injected
    ``service`` (the AgentService use-case). It is built by main/container.py — it no
    longer composes anything itself."""

    config: Config
    service: AgentService                          # injected use-case (does the work)
    browser_manager: object | None = None          # injected; closed on shutdown
    clients: set[ServerConnection] = field(default_factory=set)
    runs: dict[str, RunHandle] = field(default_factory=dict)  # session_key -> handle
    idempotency: dict[str, str] = field(default_factory=dict)  # key -> run_id

    # ------------------------------------------------------------------ serve

    async def serve(self) -> None:
        async with serve(self._handle_conn, self.config.host, self.config.port):
            log.info("listening on ws://%s:%s", self.config.host, self.config.port)
            print(f"agentd listening on ws://{self.config.host}:{self.config.port}")
            print(f"model: {self.config.model} | workspace: {self.config.workspace}")
            try:
                await asyncio.Future()  # run forever
            finally:
                if self.browser_manager is not None:
                    await self.browser_manager.close()

    async def _handle_conn(self, ws: ServerConnection) -> None:
        self.clients.add(ws)
        try:
            async for raw in ws:
                try:
                    frame = parse_frame(raw)
                except ProtocolError as e:
                    await ws.send(dump_frame(Response(id="", ok=False, payload={"error": str(e)})))
                    continue
                if isinstance(frame, Request):
                    response = await self._dispatch(frame)
                    await ws.send(dump_frame(response))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)

    # --------------------------------------------------------------- dispatch

    async def _dispatch(self, req: Request) -> Response:
        try:
            if req.method == "chat.send":
                payload = await self._chat_send(req.params)
            elif req.method == "chat.abort":
                payload = await self._chat_abort(req.params)
            elif req.method == "sessions.list":
                payload = {"sessions": list_sessions(self.config.state_dir)}
            else:
                return Response(id=req.id, ok=False, payload={"error": f"unknown method: {req.method}"})
            return Response(id=req.id, ok=True, payload=payload)
        except Exception as e:
            log.exception("dispatch error for %s", req.method)
            return Response(id=req.id, ok=False, payload={"error": f"{type(e).__name__}: {e}"})

    async def _chat_send(self, params: dict) -> dict:
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
        handle = RunHandle(run_id=run_id, session_key=session_key, abort=asyncio.Event())
        handle.task = asyncio.create_task(self._run(handle, message))
        self.runs[session_key] = handle
        return {"runId": run_id}

    async def _chat_abort(self, params: dict) -> dict:
        session_key = params.get("sessionKey") or "default"
        handle = self.runs.get(session_key)
        if handle is None or handle.task is None or handle.task.done():
            return {"aborted": False, "reason": "no active run"}
        handle.abort.set()
        handle.task.cancel()
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
