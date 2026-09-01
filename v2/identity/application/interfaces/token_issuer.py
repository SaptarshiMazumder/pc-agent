"""TokenIssuer — mint and verify access tokens, and publish the keys others verify with.

``public_jwks`` IS THE MOST IMPORTANT METHOD HERE even though nothing in this service calls it.
It is what the daemon and the model proxy fetch so they can verify signatures LOCALLY, which is
the change that takes the accounts service out of the hot path of every model call. Without it we
would have swapped one round trip (``/resolve``) for another (``/introspect``) and gained nothing.

VERIFICATION IS ALGORITHM-AGNOSTIC BY CONTRACT. The verifier reads ``kid`` from the token header,
finds that key, and uses the algorithm the KEY declares — it never assumes the algorithm the
issuer happens to be configured for. That is not gold-plating: Cognito signs RS256 while we default
to EdDSA, so an EdDSA-only verifier would have to be rewritten on the day we swap providers.
Written this way, the swap is a URL change.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from identity.domain.principal import Principal
from identity.domain.token import AccessClaims


@runtime_checkable
class TokenIssuer(Protocol):
    def issue(self, principal: Principal, *, audience: tuple[str, ...] = ()) -> tuple[str, AccessClaims]:
        """Mint an access token for ``principal``. Returns the encoded token AND the claims that
        went into it, so the caller can report ``expires_in`` without decoding what it just made."""
        ...

    def verify(self, token: str) -> AccessClaims:
        """Check signature, issuer, audience and expiry. Raises ``TokenExpired`` for a token that
        was fine but is past ``exp``, ``TokenInvalid`` for everything else — the two are separate
        because an expired token means "refresh silently" and an invalid one means "sign in again".
        """
        ...

    def public_jwks(self) -> dict:
        """The JWKS document: every non-expired PUBLIC key, keyed by ``kid``.

        Serves ALL live keys, not just the active one, because that is what makes rotation
        zero-downtime — tokens signed by the outgoing key must stay verifiable until they expire.
        """
        ...
