"""MCP transports in per-chat Fargate tasks; no server runs in the shared daemon."""
from __future__ import annotations

from dataclasses import asdict

from agent_runtime.domain.mcp import McpCallResult, McpToolSpec
from agent_runtime.domain.messages import ImageContent, TextContent
from agent_runtime.infrastructure.tools.sandbox.isolated_session import (
    IsolatedSession,
    session_owner,
)

from .tool import McpTool


def content_blocks(blocks):
    return [ImageContent(**block) if block.get("type") == "image" else TextContent(**block) for block in blocks]


class ScopedMcpSession:
    def __init__(self, config, server):
        self.config, self.server = config, server
        self.sessions = {}

    def session(self):
        owner = session_owner()
        if owner not in self.sessions:
            self.sessions[owner] = IsolatedSession(self.config, "mcp", asdict(self.server))
        return self.sessions[owner]

    async def list_tools(self):
        return [McpToolSpec(**spec) for spec in await self.session().call("list_tools", {})]

    async def call_tool(self, name, arguments):
        result = await self.session().call(name, arguments)
        return McpCallResult(content=content_blocks(result.get("content", [])),
                             details=result.get("details"), is_error=result.get("is_error", False))

    async def close(self):
        for session in self.sessions.values():
            await session.close()
        self.sessions.clear()


async def connect_isolated(config, server):
    session = ScopedMcpSession(config, server)
    specs = await session.list_tools()
    return [McpTool(session, spec, f"{server.name}__{spec.name}") for spec in specs
            if not server.allow or spec.name in server.allow]
