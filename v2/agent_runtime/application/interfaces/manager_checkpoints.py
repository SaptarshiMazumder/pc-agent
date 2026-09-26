"""ManagerCheckpoints — the only thing the engine knows about the project manager.

The loop reports each tool call and asks at three moments whether the manager has something to
say. It also reports how full the model's context is, asks for the VIEW of history to send (the
manager swaps old history for a handoff once it fills), and asks before ending a run on its
iteration cap whether unfinished contracted work may have more. A returned string is injected
into the run as-is (it already carries MANAGER_PREFIX); None means carry on. Whether a moment is worth a manager call at all — small turns never are — is the
implementation's decision, not the engine's.

ManagerRunScope is what the composition root needs to build one for a run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agent_runtime.domain.messages import Message


class ManagerCheckpoints(Protocol):
    def record_tool(self, name: str, args: dict, is_error: bool, result_text: str) -> None: ...

    async def on_start(self, messages: list[Message]) -> str | None: ...

    async def after_step(self, messages: list[Message], drift: list[str]) -> str | None: ...

    async def before_finish(self, messages: list[Message]) -> str | None: ...

    def note_context(self, fraction_used: float) -> None: ...

    def view(self, messages: list[Message]) -> list[Message]: ...

    def extend_iterations(self) -> bool: ...


@dataclass(frozen=True)
class ManagerRunScope:
    agent: Any  # AgentSpec — whose run this is
    session: Any  # SessionStore — the ledger lives beside its transcript
    tools: list  # the run's own tools; proofs run through them, as the same caller
    model: str | None  # the agent's brain model (None = the engine default)
    emit: Callable[[str, dict], Awaitable[None]]  # (event type, payload) -> surfaced to clients


__all__ = ["ManagerCheckpoints", "ManagerRunScope"]
