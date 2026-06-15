"""EventSink — the contract for "where the agent sends progress events".

The agent loop is handed one of these and calls it for every AgentEvent (text
deltas, tool start/end, agent_end, ...). Whatever is on the other end decides what
to do: the gateway broadcasts to UI clients; a test collects them in a list. The
loop doesn't know or care which — it only knows "I can emit an event here."
"""

from __future__ import annotations

from typing import Awaitable, Callable

from agentd.domain.events import AgentEvent

# An EventSink is any async callable that accepts an AgentEvent.
EventSink = Callable[[AgentEvent], Awaitable[None]]
