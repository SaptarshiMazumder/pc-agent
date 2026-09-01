"""Channels — concrete messaging transports + the poller (Phase 5b).

``MemoryChannel`` is the in-process reference/test channel; ``EmailChannel`` bridges
the Gmail MCP. ``ChannelPoller`` is the shared inbound loop. ``build_channel`` maps a
config dict to a Channel. Add a transport = drop a new adapter here + a `type` case.
"""

from __future__ import annotations

from agent_runtime.infrastructure.channels.memory_channel import MemoryChannel
from agent_runtime.infrastructure.channels.poller import ChannelPoller

__all__ = ["MemoryChannel", "ChannelPoller", "build_channel"]


def build_channel(cfg: dict, invoke):
    """Build one Channel from its config dict. `invoke(tool_name, params)->str` lets a
    channel call MCP tools (e.g. Gmail). Returns None for an unknown/disabled type."""
    ctype = (cfg.get("type") or "").lower()
    agent_id = (cfg.get("agent") or "main").strip() or "main"
    if ctype == "email":
        from agent_runtime.infrastructure.channels.email_channel import EmailChannel

        return EmailChannel(invoke, agent_id=agent_id, cfg=cfg)
    if ctype == "memory":  # test/local only
        return MemoryChannel(agent_id=agent_id)
    if ctype == "line":
        import os

        from agent_runtime.infrastructure.channels.line_channel import LineChannel

        # secrets come from env (never the config file); a per-channel `env_suffix` lets
        # multiple LINE accounts coexist, e.g. LINE_CHANNEL_SECRET_OWNER. Config keys
        # `channel_secret`/`access_token` override env if explicitly set.
        sfx = (cfg.get("env_suffix") or "").strip()
        sfx = f"_{sfx.upper()}" if sfx else ""
        secret = cfg.get("channel_secret") or os.environ.get(f"LINE_CHANNEL_SECRET{sfx}", "")
        token = cfg.get("access_token") or os.environ.get(f"LINE_CHANNEL_ACCESS_TOKEN{sfx}", "")
        if not (secret and token):
            raise ValueError(
                f"line channel needs LINE_CHANNEL_SECRET{sfx} + LINE_CHANNEL_ACCESS_TOKEN{sfx} "
                "(or channel_secret/access_token in config)"
            )
        return LineChannel(
            agent_id=agent_id,
            channel_secret=secret,
            access_token=token,
            policy=cfg.get("policy", "open"),
            allow_from=cfg.get("allow_from", []),
            webhook_path=cfg.get("webhook_path", "/line/webhook"),
        )
    return None
