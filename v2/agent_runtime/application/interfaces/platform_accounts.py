"""PlatformAccounts port — sign a person in against the platform's accounts service.

One adapter: ``infrastructure/platform_accounts_http.py``. The application depends on this
Protocol so the sign-in use case can be exercised without a network, and so the daemon — not
the browser — is the thing that ever holds a password.

DELIBERATELY NOT ``AuthProfileStore`` (interfaces/auth.py). That port rotates PROVIDER keys and
answers "which OpenAI credential should this call use". This one answers "who is the human".
"""

from __future__ import annotations

from typing import Protocol

from agent_runtime.domain.account_session import AccountSession


class PlatformAccounts(Protocol):
    @property
    def available(self) -> bool:
        """Is there an accounts service configured at all? False on an install that has none —
        the only legitimate reason for a UI to omit a sign-in prompt."""
        ...

    async def login(self, email: str, password: str, signup: bool = False) -> AccountSession:
        """Exchange credentials for a session.

        RAISES on a rejected credential or an unreachable service. A failed sign-in must never
        come back as an anonymous session: the caller cannot tell that apart from "signed out",
        and the person typing their password would be shown a login screen with no error.
        """
        ...
