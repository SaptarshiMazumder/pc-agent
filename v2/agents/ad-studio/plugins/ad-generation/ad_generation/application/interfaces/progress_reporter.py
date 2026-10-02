"""Where a long run says what it is doing, so the user watches it move instead of waiting blind."""

from __future__ import annotations

from typing import Protocol


class ProgressReporter(Protocol):
    def say(self, message: str) -> None: ...
