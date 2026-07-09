"""MCP value objects — pure data, no IO.

These describe what an MCP server advertises (`McpToolSpec`) and returns
(`McpCallResult`). They live in the domain so both the application interface and
the infrastructure implementation can speak them without depending on each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentd.domain.messages import ContentBlock


@dataclass(frozen=True)
class McpToolSpec:
    """One tool advertised by an MCP server (from tools/list)."""

    server: str  # the configured server name (used as the namespace)
    name: str  # the bare tool name as the server knows it
    description: str
    input_schema: dict  # JSON Schema for the tool's arguments (drop-in for validation)


@dataclass
class McpCallResult:
    """The result of an MCP tools/call, already mapped to domain content blocks."""

    content: list[ContentBlock] = field(default_factory=list)
    is_error: bool = False
    details: Any = None
