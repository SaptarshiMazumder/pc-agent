"""MCP sessions backed by the official `mcp` Python SDK (stdio + streamable-HTTP).

This is the ONE place that touches the SDK. The SDK's transport clients + the
ClientSession are anyio async-context-managers whose cancel scopes must be entered
and exited in the SAME task. So we run the connection inside a dedicated "runner"
task that owns those context managers and serves list/call/close requests off a
queue — every interaction with the live session happens in that one task. Callers
just await a future.

`_RunnerMcpSession` holds all the transport-INDEPENDENT machinery; a subclass only
supplies `_client_cm()` — the transport-specific async context manager that yields
the `(read, write)` streams. `StdioMcpSession` launches a subprocess;
`StreamableHttpMcpSession` connects to a hosted URL (e.g. Parallel's Search MCP).

Lazy-imports the SDK so the rest of agentd works without `mcp` installed; the
factory returns None in that case, so MCP is simply absent.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from agentd.domain.mcp import McpCallResult, McpToolSpec
from agentd.domain.messages import TextContent

log = logging.getLogger("agentd")

_CLOSE = object()  # sentinel command -> exit the runner cleanly


def resolve_subprocess(cfg):
    """Build (command, env) for an MCP stdio subprocess.

    The subprocess INHERITS agentd's environment (which includes everything loaded
    from v2/.env), so secrets like GOOGLE_OAUTH_CLIENT_ID can live in .env and never
    appear in the config. The optional `env` block in the config overlays on top,
    and any ``${VAR}`` in the command or env values is expanded from the environment
    (handy for mapping a differently-named .env var onto what the server expects).
    """

    def expand(v):
        return os.path.expandvars(v) if isinstance(v, str) else v

    command = [expand(c) for c in (cfg.command or [])]
    overlay = {k: expand(v) for k, v in (cfg.env or {}).items()}
    return command, {**os.environ, **overlay}


class _RunnerMcpSession:
    """Transport-independent MCP connection, driven through a single runner task."""

    def __init__(self, cfg):
        self._cfg = cfg
        self._q: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._error: Exception | None = None
        self._session = None  # the live SDK ClientSession (set inside the runner)

    # ---- transport hook (subclasses implement) ------------------------------

    def _client_cm(self):
        """Return the transport async-context-manager yielding `(read, write)`.

        Called INSIDE the runner task so the anyio cancel scope is entered/exited in
        one task. Lazy-imports the SDK transport here, not at module load.
        """
        raise NotImplementedError

    # ---- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Connect, initialize, and wait until ready."""
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self._cfg.name}")
        await self._ready.wait()
        if self._error is not None:
            raise self._error

    async def _run(self) -> None:
        try:
            from mcp import ClientSession

            async with self._client_cm() as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._serve()  # loop until a _CLOSE command arrives
        except Exception as e:  # noqa: BLE001 — surface startup failures to start()
            self._error = e
            self._ready.set()

    async def _serve(self) -> None:
        while True:
            make_coro, fut = await self._q.get()
            if make_coro is _CLOSE:
                if not fut.done():
                    fut.set_result(None)
                return
            try:
                fut.set_result(await make_coro())
            except Exception as e:  # noqa: BLE001 — relay to the awaiting caller
                if not fut.done():
                    fut.set_exception(e)

    async def _dispatch(self, make_coro):
        """Run a coroutine-factory inside the runner task and await its result."""
        if self._task is None or self._task.done():
            raise RuntimeError(f"MCP session '{self._cfg.name}' is not running")
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._q.put((make_coro, fut))
        return await fut

    # ---- McpSession interface ----------------------------------------------

    async def list_tools(self) -> list[McpToolSpec]:
        resp = await self._dispatch(lambda: self._session.list_tools())
        return [
            McpToolSpec(
                server=self._cfg.name,
                name=t.name,
                description=getattr(t, "description", "") or "",
                input_schema=getattr(t, "inputSchema", None) or {},
            )
            for t in resp.tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> McpCallResult:
        resp = await self._dispatch(lambda: self._session.call_tool(name, arguments))
        blocks = []
        for c in getattr(resp, "content", None) or []:
            text = getattr(c, "text", None)
            blocks.append(
                TextContent(text=text if text is not None else f"[{getattr(c, 'type', 'content')}]")
            )
        return McpCallResult(content=blocks, is_error=bool(getattr(resp, "isError", False)))

    async def close(self) -> None:
        if self._task is None or self._task.done():
            return
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        try:
            await self._q.put((_CLOSE, fut))
            await asyncio.wait_for(self._task, timeout=5.0)
        except Exception:  # noqa: BLE001 — force it down if it won't exit cleanly
            self._task.cancel()


class StdioMcpSession(_RunnerMcpSession):
    """MCP over a launched subprocess (stdio transport)."""

    def _client_cm(self):
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        cmd, env = resolve_subprocess(self._cfg)  # inherit .env, expand ${VARS}
        return stdio_client(StdioServerParameters(command=cmd[0], args=cmd[1:], env=env))


class StreamableHttpMcpSession(_RunnerMcpSession):
    """MCP over a hosted streamable-HTTP endpoint (e.g. Parallel's Search MCP)."""

    def _client_cm(self):
        from mcp.client.streamable_http import streamablehttp_client

        url = self._cfg.url
        headers = self._cfg.headers or None

        @asynccontextmanager
        async def cm():
            # streamablehttp_client yields a 3-tuple (read, write, get_session_id);
            # the runner only needs the first two streams.
            async with streamablehttp_client(url, headers=headers) as streams:
                yield streams[0], streams[1]

        return cm()


# Backward-compatible alias: stdio was the original (and only) transport.
SdkMcpSession = StdioMcpSession


async def create_sdk_session(cfg):
    """Async session factory: build + connect a stdio session."""
    session = StdioMcpSession(cfg)
    await session.start()
    return session


async def create_http_session(cfg):
    """Async session factory: build + connect a streamable-HTTP session."""
    session = StreamableHttpMcpSession(cfg)
    await session.start()
    return session


async def create_session(cfg):
    """Dispatch the session factory by transport (`stdio` default, or `http`)."""
    if getattr(cfg, "transport", "stdio") == "http":
        return await create_http_session(cfg)
    return await create_sdk_session(cfg)
