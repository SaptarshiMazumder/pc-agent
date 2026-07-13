"""Channels — a bidirectional messaging transport binding the agent to the outside
world (Phase 5b): WhatsApp / Slack / email / a local test channel.

An InboundMessage is one message arriving FROM a peer on a channel. The gateway maps
it to a conversation-bound session ``agent:<id>:<channel>:<peer>`` (Phase 1 key
scheme), runs the agent, and sends the reply back out the SAME channel. Pure domain:
no IO — the concrete transports live in infrastructure/channels/.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InboundMessage:
    channel: str  # channel name, e.g. "email", "whatsapp", "memory"
    peer: str  # who sent it (address / handle / chat id) — the reply target
    text: str  # the message body
    external_id: str = ""  # the transport's id (dedupe: don't reprocess the same message)
    ts: float = 0.0
