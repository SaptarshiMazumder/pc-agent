"""Identity failures, as distinct types.

WHY TYPES AND NOT STATUS CODES. The domain must not know it is behind HTTP — the same failure has
to be reportable over a CLI, a socket, or a future gRPC surface. The router maps these to codes in
exactly one place (``presentation/auth_router.py``), so a new caller cannot invent its own mapping
and a status code cannot drift away from what actually happened.

The distinction that matters most is CONFIGURATION vs CREDENTIAL. A bad password affects one user
and is a normal Tuesday; an unbuildable provider affects EVERY user and must stop the service
loudly rather than degrade into some other auth mode. That is the same reasoning
``payments/main/payment_gateway_factory.py`` uses to raise on an unknown rail name.
"""

from __future__ import annotations


class IdentityError(RuntimeError):
    """Base for everything this module raises."""


class IdentityConfigurationError(IdentityError):
    """The identity stack cannot be built or used as configured.

    OUR misconfiguration, not a user's mistake: an unknown provider name, a missing signing key,
    an unreadable key-encryption key. It affects every sign-in, so it must be loud and fatal
    rather than falling back to something that happens to work.
    """


class AuthenticationFailed(IdentityError):
    """The credential did not check out.

    DELIBERATELY UNDIFFERENTIATED between "no such account" and "wrong password". Telling the two
    apart turns the login form into an account-enumeration oracle, which is exactly the lookup an
    attacker wants before a credential-stuffing run.
    """


class AccountDisabled(IdentityError):
    """The credential was correct but the account is deactivated (``accounts.active = 0``)."""


class TokenInvalid(IdentityError):
    """A presented token is not one we will accept: bad signature, wrong issuer or audience,
    unknown ``kid``, or structurally not a token at all."""


class TokenExpired(TokenInvalid):
    """Structurally valid, correctly signed, past its ``exp``.

    Separate from ``TokenInvalid`` because the CLIENT REACTION differs and that difference is the
    whole point of short-lived access tokens: expired means "refresh and retry silently", while
    invalid means "sign in again". Collapsing them would make every expiry look like a session
    loss to the user.
    """


class RefreshReuseDetected(IdentityError):
    """A refresh token that had already been rotated was presented again.

    Treated as theft, not as a mistake: the legitimate holder rotates once and forgets the old
    value, so a second use means two parties hold the same token. The whole family is revoked —
    see ``application/interfaces/refresh_store.py`` for why that is the correct blast radius.
    """
