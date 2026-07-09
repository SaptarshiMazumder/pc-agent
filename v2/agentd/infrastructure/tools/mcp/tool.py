"""McpTool — adapts ONE MCP-server tool to the agent's Tool contract.

One class covers every tool from every server (Gmail, Drive, Slack, ...). It holds
its server `McpSession` (an interface — DIP) and the tool's spec, and forwards
`execute` to the session. It IS a `Tool`, so it is guarded, validated, advertised,
and called exactly like a native tool (LSP). Never raises — errors become an error
ToolResult (the same contract every tool follows).
"""

from __future__ import annotations

import asyncio

from agentd.application.interfaces.mcp import McpSession
from agentd.domain.mcp import McpToolSpec

from .. import Tool, ToolResult
from .mapping import to_tool_result


class McpTool(Tool):
    # Reliability defaults consumed by resolve_policy() when GuardedTool wraps this.
    default_timeout_sec = 120.0  # MCP calls hit external APIs; allow headroom
    default_retryable = False  # external side effects — don't auto-retry
    concurrency = "parallel"

    def __init__(self, session: McpSession, spec: McpToolSpec, namespaced_name: str):
        self._session = session
        self._spec = spec
        self.name = namespaced_name  # e.g. "google__gmail_send"
        self.description = spec.description or f"MCP tool '{spec.name}' from '{spec.server}'."
        self.parameters = spec.input_schema or {"type": "object", "properties": {}}
        self.label = namespaced_name

    async def execute(self, tool_call_id, params, abort, on_update=None) -> ToolResult:
        try:
            result = await self._session.call_tool(self._spec.name, params or {})
        except asyncio.CancelledError:
            raise  # abort propagates; never swallow cancellation
        except Exception as e:  # noqa: BLE001 — never raise; surface as an error result
            return ToolResult.text(f"{self.name} failed: {type(e).__name__}: {e}", is_error=True)
        return to_tool_result(result)
