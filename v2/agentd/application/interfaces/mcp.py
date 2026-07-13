"""McpSession — the contract for ONE live connection to ONE MCP server.

The MCP tool adapter and provider depend on THIS interface, never on a concrete
transport or the `mcp` SDK (DIP). It references only domain value objects, so it
stays in the application layer without reaching into infrastructure.
"""

from __future__ import annotations

from typing import Protocol

from agentd.domain.mcp import McpCallResult, McpToolSpec


class McpSession(Protocol):
    """A ready (connected + initialized) session to one MCP server."""

    async def list_tools(self) -> list[McpToolSpec]:
        """The tools the server advertises."""
        ...

    async def call_tool(self, name: str, arguments: dict) -> McpCallResult:
        """Invoke one of the server's tools by its bare name."""
        ...

    async def close(self) -> None:
        """Tear the connection down."""
        ...
