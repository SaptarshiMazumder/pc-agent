"""OAuth value objects — what an agent declares, and what came back.

Pure data. No HTTP, no clock, no storage: the application service does the flow, infrastructure
does the sockets, and this file only says what the pieces ARE.

WHY OAUTH IS A FIRST-CLASS THING HERE and not a corner of the MCP code. Most services worth
connecting to have no API key to paste — you sign in, and they hand back a token that expires.
That is true of an MCP server, of a plain REST API a private tool calls, and of anything an agent
will want next year. So the declaration, the store and the refresh live in one place, and both
consumers read from it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class OAuthDecl:
    """An OAuth connection an agent needs, declared in ``agent.toml`` (``[[oauth]]``).

    Ships with the package like every other declaration. What it never carries is the USER's
    identity — that is a token on one machine, obtained by that person signing in.

    TWO KINDS OF PROVIDER, one declaration:

      * a server with published metadata (``server`` set) — the endpoints are DISCOVERED, so the
        author writes three lines and the person installing it clicks Connect.
      * a classic provider (``authorize_url`` + ``token_url``) — Google, Notion, Coinbase. These
        require an app registered up front, so ``client_id``/``client_secret`` reference
        ``[[settings]]`` keys and the INSTALLER supplies their own. An author who hard-codes
        theirs is shipping their identity to every buyer.
    """

    name: str  # what tools and [[mcp]] blocks reference: oauth:<name>
    server: str = ""  # discovery root (RFC 8414) — or use the two explicit urls
    authorize_url: str = ""
    token_url: str = ""
    scopes: tuple[str, ...] = ()
    client_id: str = ""  # may be ${SETTING}
    client_secret: str = ""  # may be ${SETTING}; omitted for a public PKCE client

    @property
    def discoverable(self) -> bool:
        return bool(self.server) and not (self.authorize_url and self.token_url)


@dataclass(frozen=True)
class OAuthTokens:
    """What the provider handed back, plus when it stops being useful.

    ``expires_at`` is an ABSOLUTE epoch second, computed once from the provider's relative
    ``expires_in``. Storing the relative number would make every read a question about when it was
    written, and the answer is exactly the sort of thing that is wrong after a laptop sleeps.
    """

    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0  # 0 = the provider did not say, so treat it as non-expiring
    scopes: tuple[str, ...] = ()
    account: str = ""  # a label for the connected identity, when the provider says who it is

    def stale(self, now: float | None = None, skew: float = 60.0) -> bool:
        """Is this token due for refresh?

        ``skew`` exists because "expires at 12:00:00" and a request that leaves at 11:59:59.8 is
        a 401 nobody can reproduce. Sixty seconds is cheap insurance against that and against a
        clock that drifts.
        """
        if not self.expires_at:
            return False
        return (now if now is not None else time.time()) >= self.expires_at - skew

    @property
    def usable(self) -> bool:
        return bool(self.access_token)
