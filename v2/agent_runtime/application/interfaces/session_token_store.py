"""SessionTokenStore port — where this install remembers who is signed in.

Separate from the model-proxy credential ON PURPOSE. Identity and payment are different facts
about a machine, and one install can hold either without the other: signed in on your own API
keys (identity, no billing) is a completely ordinary state that the old single-credential design
could not represent.

One adapter: ``infrastructure/env_file_session_token_store.py``.
"""

from __future__ import annotations

from typing import Protocol

from agent_runtime.domain.account_session import AccountSession


class SessionTokenStore(Protocol):
    def read(self) -> AccountSession:
        """The stored session, or ANONYMOUS. Never raises — "I could not read it" and "nobody is
        signed in" lead to the same place: show the sign-in prompt."""
        ...

    def write(self, session: AccountSession) -> None:
        """Persist a session. Raises if it cannot be stored: silently failing here signs the user
        in for exactly as long as the process lives, then logs them out with no explanation."""
        ...

    def clear(self) -> None: ...
