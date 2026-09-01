"""KeyStore — the signing keys, and the rotation they make possible.

WHY KEYS ARE IN THE DATABASE AND NOT IN AN ENV VAR. Several services must agree on them, they must
survive a restart, and they must be ROTATABLE without a deploy. An env var fails the last one: with
the key baked into the task definition, rotating it means every token signed by the old key becomes
unverifiable the instant the new container starts — a hard sign-out for everyone mid-session.

Rotation works because verification is keyed by ``kid``: the store keeps the outgoing key
verifiable (``expires_at`` in the future) while the incoming one signs. Old tokens keep working
until they expire naturally, new ones use the new key, and nobody is signed out.

THE PRIVATE HALF IS ENCRYPTED AT REST when ``AGENTD_IDENTITY_KEK`` is set, and stored plainly when
it is not. That is a deliberate two-mode design rather than a hard requirement: the key sits in the
same file as the password hashes, so on a laptop encrypting it with a secret from the same
environment adds nothing but a way for local dev to break. In a deployment where the database is
backed up somewhere the KEK is not, it is exactly the protection that matters — so the adapter logs
loudly when it is running unencrypted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SigningKey:
    """One key pair. ``private_pem`` is empty on keys read for verification only."""

    kid: str
    #: JWS algorithm name — ``EdDSA``, ``RS256``. Travels WITH the key, so a verifier never has
    #: to assume; see token_issuer.py for why that matters for the Cognito swap.
    alg: str
    public_pem: str
    private_pem: str = ""
    created_at: float = 0.0
    #: When this key stops being served for verification. 0 = no expiry set (the active key).
    expires_at: float = 0.0


@runtime_checkable
class KeyStore(Protocol):
    def active(self) -> SigningKey:
        """The key to SIGN with. Creates one on first call — a fresh deployment must be able to
        issue a token without an operator running a provisioning step first."""
        ...

    def verification_keys(self) -> list[SigningKey]:
        """Every key still valid for VERIFYING (active + not-yet-expired outgoing ones), public
        halves only. This is what becomes the JWKS document."""
        ...

    def rotate(self, *, retire_after_s: float) -> SigningKey:
        """Generate a new active key and give the outgoing one ``retire_after_s`` of remaining
        verification life. That window must exceed the access-token TTL, or tokens signed a moment
        before the rotation become unverifiable before they expire."""
        ...
