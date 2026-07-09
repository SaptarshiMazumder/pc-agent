"""WindowContextPolicy — keep the most recent turns within a message budget (S7).

Conservative truncation: when history exceeds ``max_messages``, send only the tail, started
at a clean turn boundary (a user message) so an assistant's tool-call is never split from its
tool-result. Lossy but safe and LLM-free; a summarizing policy can later replace it behind the
same ``ContextPolicy`` port without touching the engine.
"""

from __future__ import annotations

from agentd.domain.messages import Message


class WindowContextPolicy:
    def __init__(self, max_messages: int):
        self.max_messages = max(2, int(max_messages))

    def prepare(self, messages: list[Message]) -> list[Message]:
        if len(messages) <= self.max_messages:
            return messages
        window = messages[-self.max_messages :]
        for i, m in enumerate(window):  # start at a clean user-turn boundary
            if getattr(m, "role", "") == "user":
                return window[i:]
        return window  # no boundary in window (rare) -> tail as-is
