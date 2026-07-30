"""MemoryChannel — an in-process Channel: a working reference adapter + test double.

Implements the full Channel contract without any external service, so the channel
FRAMEWORK (poll -> run -> reply, conversation binding, notify bridge) is exercised
end-to-end in tests. It's also the template for a real adapter: implement ``poll`` +
``send`` for your transport and you're done.
"""

from __future__ import annotations

from agent_runtime.domain.channels import InboundMessage


class MemoryChannel:
    name = "memory"

    def __init__(self, agent_id: str = "main"):
        self.agent_id = agent_id
        self._inbox: list[InboundMessage] = []
        self._cursor = 0
        self.sent: list[tuple[str, str]] = []  # (peer, text) replies + notifications land here

    def inject(self, peer: str, text: str, external_id: str = "") -> None:
        """Simulate an inbound message arriving from ``peer``."""
        self._inbox.append(
            InboundMessage(channel=self.name, peer=peer, text=text, external_id=external_id)
        )

    async def poll(self) -> list[InboundMessage]:
        new = self._inbox[self._cursor :]  # only messages not yet seen
        self._cursor = len(self._inbox)
        return new

    async def send(self, peer: str, text: str) -> None:
        self.sent.append((peer, text))
