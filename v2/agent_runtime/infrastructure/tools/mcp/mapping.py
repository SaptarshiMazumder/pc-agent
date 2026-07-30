"""Translate between MCP results and the agent's ToolResult (one place)."""

from __future__ import annotations

from agent_runtime.domain.mcp import McpCallResult
from agent_runtime.domain.messages import TextContent

from .. import ToolResult


def to_tool_result(result: McpCallResult) -> ToolResult:
    """Map a server's call result onto the agent's standard ToolResult shape."""
    content = result.content or [TextContent(text="(no content)")]
    return ToolResult(content=content, details=result.details, is_error=result.is_error)
