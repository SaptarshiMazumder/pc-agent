"""build_mcp_provider — select + assemble the MCP provider from config.

Returns None when no servers are configured OR the optional `mcp` SDK isn't
installed, so MCP is OFF by default and a missing dependency never breaks the rest
of agentd (same opt-in pattern as the browser/computer factories).
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


def build_mcp_provider(config):
    servers = [s for s in (getattr(config, "mcp_servers", None) or [])
               if getattr(s, "enabled", True)]
    if not servers:
        return None
    try:
        import mcp  # noqa: F401 — the official SDK is an optional dependency
    except ImportError:
        log.warning("mcp_servers configured but the `mcp` SDK is not installed; "
                    "MCP disabled. Install with: pip install mcp")
        return None

    # stdio (subprocess) and http (hosted streamable-HTTP) are both supported; the
    # session factory dispatches per server by transport.
    supported = [s for s in servers if getattr(s, "transport", "stdio") in ("stdio", "http")]
    skipped = [s.name for s in servers if getattr(s, "transport", "stdio") not in ("stdio", "http")]
    if skipped:
        log.warning("MCP: unknown transports, skipping %s", skipped)
    if not supported:
        return None

    from .provider import McpProvider
    from .session import create_session

    return McpProvider(supported, create_session)
