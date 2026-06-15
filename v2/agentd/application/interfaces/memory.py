"""SessionStore — the contract for reading/writing one session's transcript.

The use-case depends on THIS interface, not on any concrete store. The local JSONL
store satisfies it today; a future cloud / end-to-end-encrypted bank will satisfy
the same interface and slot in by config, with no change to the use-case.
"""

from __future__ import annotations

from typing import Protocol

from agentd.domain.messages import Message


class SessionStore(Protocol):
    """Load the prior messages of a session, and append new ones to it."""

    def load(self) -> list[Message]: ...

    def append(self, message: Message) -> str: ...
