"""AgentEngine — the contract for the swappable "brain" (the reason->act loop).

Given a conversation, a system prompt, the tools, and a way to emit events, an engine
runs the loop to completion. ``NativeEngine`` (our hand-rolled loop) implements this;
a Claude-SDK engine or a LangGraph engine would be alternative implementations behind
this same interface — selected by config, with nothing else in the app changing.
"""

from __future__ import annotations

from typing import Protocol

from agentd.application.interfaces.events import EventSink
from agentd.domain.messages import Message


class AgentEngine(Protocol):
    async def run(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools,
        on_event: EventSink,
        abort,
        session=None,
        model: str | None = None,  # per-turn model override (None = the engine's default)
    ) -> list[Message]: ...
