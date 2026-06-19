"""McpProvider — connect to the configured MCP servers and discover their tools.

Owns the connection lifecycle for a set of servers. `discover()` opens each enabled
server (via an injected async `session_factory` — DIP/testability), lists its tools,
and returns one `McpTool` per tool, namespaced as `<server>__<tool>`. A server that
fails to connect is logged and skipped — others and the gateway are unaffected
(graceful degradation, like the browser/computer factories). `aclose()` tears down
every session on shutdown.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from agentd.application.interfaces.mcp import McpSession

from .. import Tool
from .tool import McpTool

log = logging.getLogger("agentd")

# server config -> a ready (connected + initialized) session
SessionFactory = Callable[[object], Awaitable[McpSession]]


class McpProvider:
    def __init__(self, server_configs: list, session_factory: SessionFactory):
        self._configs = server_configs
        self._session_factory = session_factory
        self._sessions: list[McpSession] = []

    async def discover(self) -> list[Tool]:
        """Connect to each enabled server and return its tools (namespaced)."""
        tools: list[Tool] = []
        for cfg in self._configs:
            if not getattr(cfg, "enabled", True):
                continue
            try:
                session = await self._session_factory(cfg)
                specs = await session.list_tools()
            except Exception as e:  # noqa: BLE001 — one bad server never breaks the rest
                log.warning("MCP server '%s' unavailable, skipping: %s",
                            getattr(cfg, "name", "?"), e)
                continue
            self._sessions.append(session)
            allow = getattr(cfg, "allow", None)
            for spec in specs:
                if allow and spec.name not in allow:
                    continue  # per-server allowlist (optional)
                tools.append(McpTool(session, spec, f"{cfg.name}__{spec.name}"))
            log.info("MCP server '%s': %d tool(s) discovered", cfg.name, len(specs))
        return tools

    async def aclose(self) -> None:
        for session in self._sessions:
            try:
                await session.close()
            except Exception:  # noqa: BLE001 — best-effort shutdown
                pass
        self._sessions.clear()
