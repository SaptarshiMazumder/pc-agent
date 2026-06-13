"""Agent lifecycle events emitted by the loop and streamed to clients.

Event sequence per run (mirrors the reference agent loop):
  agent_start
  turn_start
    message_start (assistant)
    message_update*          (streaming deltas)
    message_end (assistant)
    [per tool call:
      tool_execution_start
      tool_execution_update*
      tool_execution_end
      message_end (toolResult)]
  turn_end
  ... more turns ...
  agent_end {stopReason}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class AgentEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.payload}


# The loop accepts a single async callback; the gateway fans events out to clients.
EventCallback = Callable[[AgentEvent], Awaitable[None]]


async def null_event_sink(_event: AgentEvent) -> None:
    return None
