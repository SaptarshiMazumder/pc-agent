"""Agent lifecycle events — the live "play-by-play" of a run.

This is the DOMAIN layer (pure data, no IO). While a request is being handled, the
agent emits a stream of small ``AgentEvent`` objects describing what is happening
*right now* — text streaming in, a tool starting, the run ending, etc. The gateway
forwards these to the UI so the user sees progress live; tests capture them to assert
the agent behaved correctly.

Difference from messages.py: a ``Message`` is the durable *record* of the conversation
(saved to the transcript); an ``AgentEvent`` is a transient *notification* about
progress (not persisted) — e.g. "a text delta arrived", "a tool finished".

Event sequence emitted per run (mirrors the reference agent loop):
  agent_start
  turn_start
    message_start (assistant)
    message_update*          (streaming text/thinking deltas)
    message_end (assistant)
    [per tool call:
      tool_execution_start
      tool_execution_update*
      tool_execution_end
      message_end (toolResult)]
  turn_end
  ... more turns (the loop) ...
  agent_end {stopReason}

A nested SUB-AGENT run's beats are relayed to the PARENT's stream as a compact
``subagent_event`` (kind: start / tool / done / error), synthesized by the gateway, so a
parent that's blocked on a child shows the child working instead of going silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class AgentEvent:
    """One progress notification.

    ``type`` is the event name (e.g. "text_delta", "tool_execution_start",
    "tool_progress" (incremental tool updates: retries / timeouts / computer steps),
    "agent_end"); ``payload`` carries any extra data for that event (e.g. the text
    delta string, the tool name, the stop reason).
    """
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Flatten to a single dict for sending over the wire:
        ``{"type": <name>, **payload}`` — the type plus the payload's keys."""
        return {"type": self.type, **self.payload}


# The agent loop is given ONE async callback to emit events through; the gateway's
# implementation of it fans each event out to all connected UI clients. (In the clean
# architecture this is the "EventStream" interface — anything with an async emit.)
EventCallback = Callable[[AgentEvent], Awaitable[None]]


async def null_event_sink(_event: AgentEvent) -> None:
    """A do-nothing sink — used when no one is listening (e.g. headless runs/tests)."""
    return None
