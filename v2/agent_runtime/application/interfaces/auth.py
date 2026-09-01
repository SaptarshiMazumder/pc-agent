"""AuthProfileStore port — pick + rotate provider credentials (S16).

`available` returns the least-recently-used, non-cooled profile for a provider (rotation);
`record_success`/`record_failure` update its state (clear vs cool-down). The application
depends on this port; SQLite is one adapter.
"""

from __future__ import annotations

from typing import Protocol

from agent_runtime.domain.auth_profile import AuthProfile


class AuthProfileStore(Protocol):
    def add(self, profile: AuthProfile) -> str: ...

    def available(self, provider: str, now: float) -> AuthProfile | None:
        """The least-recently-used enabled profile for `provider` not in cooldown, or None."""
        ...

    def record_success(self, profile_id: str, now: float) -> None:
        """Mark used now; clear failures + cooldown."""
        ...

    def record_failure(self, profile_id: str, now: float, cooldown_sec: float) -> None:
        """Increment failures; cool the profile down for `cooldown_sec`."""
        ...

    def list(self, provider: str | None = None) -> list[AuthProfile]: ...
