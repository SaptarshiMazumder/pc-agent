"""The ProgressReporter port over a tool call's `on_update` — safe to call from the worker thread
the run executes in, because it hands each line to the event loop instead of calling across."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import ToolResult


class ToolProgressReporter:
    def __init__(self, loop: asyncio.AbstractEventLoop, on_update) -> None:
        self._loop = loop
        self._on_update = on_update

    def say(self, message: str) -> None:
        if self._on_update is not None:
            self._loop.call_soon_threadsafe(self._on_update, ToolResult.text(message))
