"""PlatformModeStore port — where the user's Local/Cloud choice is remembered.

IT HAS TO LIVE IN THE DAEMON. It used to live in the desktop client's ``localStorage``
(clients/ui/src/lib/mode.ts). An agent's own window is a different page with different storage, so
it could neither read the choice nor change it — which is why switching mode meant leaving the
agent, opening agentd, and switching there. Moving the fact to the daemon is what lets every UI
agree about it.

One adapter: ``infrastructure/env_file_platform_mode_store.py``.
"""

from __future__ import annotations

from typing import Protocol


class PlatformModeStore(Protocol):
    def read(self) -> str:
        """The stored preference: ``platform_mode.LOCAL``, ``CLOUD``, or ``UNSET`` when the user
        has never chosen. Never raises — an unreadable preference is the same as no preference."""
        ...

    def write(self, mode: str) -> None: ...
