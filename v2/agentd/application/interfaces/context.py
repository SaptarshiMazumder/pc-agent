"""ContextPolicy port — shape the message history sent to the model (Phase 3.5 / S7).

A long conversation eventually exceeds the model's context window. A ContextPolicy decides
what slice of history to send on a given call (truncate, summarize, …) WITHOUT mutating the
stored transcript. Default = none (send everything, unchanged); SQLite/LLM-free decision.
"""

from __future__ import annotations

from typing import Protocol

from agentd.domain.messages import Message


class ContextPolicy(Protocol):
    def prepare(self, messages: list[Message]) -> list[Message]:
        """Return the messages to send THIS call — a possibly-compacted view of history."""
        ...
