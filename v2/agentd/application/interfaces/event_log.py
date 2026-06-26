"""EventSink — the durable home for a run's live event stream (the observability seam).

Every ``AgentEvent`` a run emits (thinking, tool calls, text, end) flows through the gateway's
``on_event`` sink. Besides broadcasting to connected clients, that sink can also EMIT each event
here, so a run's full play-by-play is RECORDED even when NO client is attached — cron, channel,
heartbeat and sub-agent runs included. A viewer can then tail it live (no client needed) or
replay it after the fact.

Pure port (depends only on the domain event type); the file-backed implementation lives in
infrastructure. ``emit`` must never raise into the caller — observability must not break a run.
"""

from __future__ import annotations

from typing import Protocol

from agentd.domain.events import AgentEvent


class EventSink(Protocol):
    def emit(self, session_key: str, run_id: str, event: AgentEvent) -> None:
        """Durably record one event for a run. Best-effort; never raises."""
        ...

    def close(self) -> None:
        """Flush/close any open resources (called on gateway shutdown)."""
        ...
