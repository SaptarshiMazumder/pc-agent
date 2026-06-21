"""Channels — concrete messaging transports + the poller (Phase 5b).

``MemoryChannel`` is the in-process reference/test channel; ``EmailChannel`` bridges
the Gmail MCP. ``ChannelPoller`` is the shared inbound loop. ``build_channel`` maps a
config dict to a Channel. Add a transport = drop a new adapter here + a `type` case.
"""

from __future__ import annotations

from agentd.infrastructure.channels.memory_channel import MemoryChannel
from agentd.infrastructure.channels.poller import ChannelPoller

__all__ = ["MemoryChannel", "ChannelPoller", "build_channel"]


def build_channel(cfg: dict, invoke):
    """Build one Channel from its config dict. `invoke(tool_name, params)->str` lets a
    channel call MCP tools (e.g. Gmail). Returns None for an unknown/disabled type."""
    ctype = (cfg.get("type") or "").lower()
    agent_id = (cfg.get("agent") or "main").strip() or "main"
    if ctype == "email":
        from agentd.infrastructure.channels.email_channel import EmailChannel

        return EmailChannel(invoke, agent_id=agent_id, cfg=cfg)
    if ctype == "memory":                       # test/local only
        return MemoryChannel(agent_id=agent_id)
    return None
