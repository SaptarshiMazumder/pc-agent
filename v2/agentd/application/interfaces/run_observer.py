"""RunObserver — the LIVENESS seam ("is the run stuck / looping / not moving?").

The agent loop notifies observers around each tool call and at each turn boundary;
an observer returns a HALT REASON (a short string) when it thinks the run is stuck,
or None otherwise. The loop turns a halt reason into a steering message (and, after
repeated halts, ends the run) — the observer itself has no power over the loop
beyond signalling.

Observers are PURE: they see only `ToolEvent`s and a turn index — never the tools,
the LLM, the session, or each other. They are wired as an optional list in the
composition root; an empty list means today's behavior, unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolEvent:
    name: str                       # the tool's name
    args: dict                      # the call arguments
    phase: str                      # "before" | "after"
    is_error: bool | None = None    # after: did the tool return an error?
    result_digest: str | None = None  # after: short hash of the result text (for "new info?" checks)


@runtime_checkable
class RunObserver(Protocol):
    def on_tool(self, ev: ToolEvent) -> str | None: ...   # halt reason or None
    def on_turn(self, index: int) -> str | None: ...      # halt reason or None
    def reset(self) -> None: ...                           # called once at run start
