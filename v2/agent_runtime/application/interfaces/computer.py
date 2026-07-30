"""ComputerProvider — the contract for the OS "hands and eyes" of the computer tool.

The `computer` tool runs an internal vision loop (screenshot -> a computer-use
model picks one action -> execute -> repeat). This port is the side that touches
the real machine: capture the screen, and perform a single GUI action. The
vision MODEL that decides the actions is separate (the driver), so the OS backend
and the brain are independently swappable — Playwright-style, but for the desktop.

Today's adapter is pyautogui (local); a remote/VNC backend could implement the
same port later. Pure Protocol — no pyautogui import here (import-linter safe).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ComputerProvider(Protocol):
    # (left, top, width, height) in real pixels that 0-1000 coords normalize over.
    region: tuple[int, int, int, int]

    def screenshot(self) -> bytes:
        """PNG bytes of the captured region (downscaled for transport)."""
        ...

    def act(self, action: str, **params) -> dict:
        """Perform one action. Returns {} on success or {"error": str}. Raises only
        the failsafe (mouse-to-corner abort), which the driver treats as a stop."""
        ...
