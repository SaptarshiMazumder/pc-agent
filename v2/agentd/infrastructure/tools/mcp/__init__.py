"""MCP client layer — connect to external MCP servers and surface their tools.

Public surface: ``build_mcp_provider(config)`` (factory) and ``McpProvider`` (the
lifecycle/discovery owner). Everything else is internal. Discovered tools are
ordinary ``Tool``s (``McpTool``), so they flow through the same GuardedTool /
prompt / engine pipeline as native tools.
"""

from __future__ import annotations

from agentd.infrastructure.tools.mcp.factory import build_mcp_provider
from agentd.infrastructure.tools.mcp.provider import McpProvider

__all__ = ["build_mcp_provider", "McpProvider"]
