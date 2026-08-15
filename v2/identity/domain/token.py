"""Token value objects — what a verified access token says, and what a login hands back.

TWO TOKENS, TWO JOBS, and they are not interchangeable:

  * the ACCESS token is a signed JWT, short-lived, and verified WITHOUT touching a database. That
    is the point of the whole design: today every uncached model call makes the model proxy do a
    ``/resolve`` round trip to the accounts service, which is why ``custom_auth.py`` carries a
    stale-serving grace hack to survive blips. A signature check against a cached public key
    removes the accounts service from the hot path entirely.

  * the REFRESH token is opaque, long-lived, stored HASHED, and single-use. It never travels to
    the model proxy or the daemon — only to ``/auth/refresh``. Compromising an access token buys
    minutes; the refresh token is the one that matters, so it goes to exactly one endpoint.

The price of local verification, stated plainly: a revoked ACCESS token stays valid until its
``exp``. With a ten-minute TTL that is the containment window. Refresh revocation is immediate, so
a compromised account stops being able to mint new access tokens at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from identity.domain.principal import Principal


@dataclass(frozen=True)
class AccessClaims:
    """A verified access token, decoded. Produced only by a successful verification."""

    account_id: str  # the `sub` claim
    email: str = ""
    email_verified: bool = False
    scopes: tuple[str, ...] = field(default_factory=tuple)
    amr: tuple[str, ...] = field(default_factory=tuple)
    session_id: str = ""
    issuer: str = ""
    audience: tuple[str, ...] = field(default_factory=tuple)
    issued_at: float = 0.0
    expires_at: float = 0.0
    #: Unique per token. Exists so a future denylist has something to name; unused in P1.
    jti: str = ""

    def principal(self) -> Principal:
        return Principal(
            account_id=self.account_id,
            email=self.email,
            email_verified=self.email_verified,
            scopes=self.scopes,
            amr=self.amr,
            session_id=self.session_id,
        )


@dataclass(frozen=True)
class TokenPair:
    """What a successful login or refresh returns.

    ``expires_in`` is seconds, not an absolute time, and that is not a style choice: the client's
    clock may disagree with ours, and a relative lifetime is correct under skew where an absolute
    deadline is not. The client schedules its refresh off this.
    """

    access_token: str
    refresh_token: str
    expires_in: int
    account_id: str
    email: str = ""
    token_type: str = "Bearer"
    session_id: str = ""

    def as_response(self) -> dict:
        """The wire shape for ``/auth/login`` and ``/auth/refresh`` (OAuth2 field names, so a
        generic HTTP client or a future SDK needs no translation layer)."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "account_id": self.account_id,
            "email": self.email,
        }


@dataclass(frozen=True)
class RefreshRecord:
    """One row of the refresh chain, as the store hands it back after a successful consume."""

    row_id: int
    account_id: str
    #: One login = one family. Rotation walks forward within it; revocation kills all of it.
    family_id: str
    client_id: str = ""
    device_label: str = ""
    issued_at: float = 0.0
    expires_at: float = 0.0
    family_expires_at: float = 0.0
