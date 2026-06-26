"""ConnectTokenStore — one-time, expiring tokens for the secure /connect login form.

When an agent has no saved login for a site, simple_login mints a token here and hands the user
a tappable link (``<public_url>/connect/<token>``). The user opens it, fills a TLS form, and the
webhook server validates+consumes the token and writes the credential to the vault — the password
goes form → server → vault, never through the chat/model. In-memory (tokens are short-lived; they
needn't survive a restart).
"""

from __future__ import annotations

import secrets
import time


class ConnectTokenStore:
    def __init__(self, ttl_seconds: float = 900.0):
        self._ttl = float(ttl_seconds)
        self._tokens: dict[str, tuple[str, str, float]] = {}   # token -> (agent, site, expiry)

    def mint(self, agent_id: str, site: str) -> str:
        token = secrets.token_urlsafe(24)                      # unguessable, single-use
        self._tokens[token] = (agent_id, site, time.time() + self._ttl)
        return token

    def resolve(self, token: str) -> tuple[str, str] | None:
        """(agent, site) for a valid, unexpired token; else None (expired ones are dropped)."""
        entry = self._tokens.get(token)
        if not entry:
            return None
        agent, site, expiry = entry
        if time.time() > expiry:
            self._tokens.pop(token, None)
            return None
        return (agent, site)

    def consume(self, token: str) -> tuple[str, str] | None:
        """Like resolve, but single-use: a valid token is removed so it can't be reused."""
        result = self.resolve(token)
        if result is not None:
            self._tokens.pop(token, None)
        return result

    @staticmethod
    def link(public_url: str, token: str) -> str:
        return f"{public_url.rstrip('/')}/connect/{token}"
