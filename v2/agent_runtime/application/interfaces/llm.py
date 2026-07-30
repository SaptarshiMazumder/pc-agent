"""LLMService — the contract for "stream a model reply".

This is the shape of the stream function the engine calls once per loop iteration.
``litellm.litellm_stream`` (and ``functools.partial`` of it) satisfy it structurally;
a fake used in tests also satisfies it. The engine depends on this shape, not on LiteLLM.

It yields a stream of dict events:
  {"type":"text_delta"|"thinking_delta", "delta": str}
  {"type":"toolcall_end", "toolCall": {...}}
  {"type":"done", "message": AssistantMessage}
and by contract NEVER raises (errors/aborts become a "done" with the right stop_reason).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from agent_runtime.domain.messages import Message


class LLMService(Protocol):
    def __call__(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[Message],
        tools,
        abort,
    ) -> AsyncIterator[dict[str, Any]]: ...
