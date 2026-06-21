"""Channel port — a bidirectional messaging transport (Phase 5b).

ONE interface per transport. A transport is implemented ONCE and reused three ways:
  * ``poll()``           inbound — new messages since last poll (the channel dedupes)
  * ``send(peer, text)`` outbound — agent replies AND notifications both go through this
So adding WhatsApp/Slack = one Channel adapter; notifications through it come free via
``ChannelNotifier`` (no second implementation). ``agent_id`` binds the channel to the
agent that handles its inbound traffic.
"""

from __future__ import annotations

from typing import Protocol

from agentd.domain.channels import InboundMessage


class Channel(Protocol):
    name: str            # transport name (also the <channel> segment of the session key)
    agent_id: str        # the agent that handles this channel's inbound messages

    async def poll(self) -> list[InboundMessage]:
        """Return messages that have arrived since the last poll (already deduped)."""
        ...

    async def send(self, peer: str, text: str) -> None:
        """Deliver ``text`` to ``peer`` over this transport. Used by replies AND notify."""
        ...
