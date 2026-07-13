"""CredentialStore — the port for per-(agent, site) saved logins.

The vault holds each agent's site credentials encrypted at rest. The login tool reads a
``Credential`` from here to fill a form; an agent can only see its OWN sites. The encrypted-file
implementation lives in infrastructure; a secrets-manager backend could replace it behind this
same port without touching the tool.
"""

from __future__ import annotations

from typing import Protocol

from agentd.domain.credential import Credential


class CredentialStore(Protocol):
    def get(self, agent_id: str, site: str) -> Credential | None:
        """The agent's saved login for ``site``, or None."""
        ...

    def put(self, agent_id: str, cred: Credential) -> None:
        """Save/replace the agent's login for ``cred.site``."""
        ...

    def delete(self, agent_id: str, site: str) -> bool:
        """Remove one saved login; True if it existed."""
        ...

    def list(self, agent_id: str) -> list[str]:
        """The agent's saved site names (NO secrets)."""
        ...

    def purge_agent(self, agent_id: str) -> int:
        """Remove ALL of one agent's saved logins (used when an agent is deleted)."""
        ...
