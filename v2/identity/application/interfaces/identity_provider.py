"""IdentityProvider — the thing that vouches for a human.

WHAT A PROVIDER RETURNS IS AN ASSERTION, NOT AN ACCOUNT. "This is Google's user 11746…, whose
verified email is x@y.com" is all a provider knows; turning that into an ``acct_`` id is the
platform's job, not the provider's (``application/services/principal_service.py``). Keeping that
boundary is what lets one account hold several logins, and what stops a provider from being able
to hand itself somebody else's account.

``from_external_assertion`` IS DECLARED AND UNIMPLEMENTED FOR THE LOCAL PROVIDER, on purpose —
the same move ``payments`` makes with ``payout``. Declaring it now means every provider we consider
is asked the question, and it is the method P4 fills in for Google/Microsoft/Cognito. It is not a
promise that external login works today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Assertion:
    """A provider's claim about who just authenticated."""

    #: ``local`` | ``google`` | ``microsoft`` | ``cognito`` — the key half of the identity record.
    provider: str
    #: The provider's OWN stable id for this human. For the local provider this is the account id
    #: (there is nothing more stable to use). For an OIDC provider it is the ``sub`` claim —
    #: NEVER the email, which users change and providers reassign.
    subject: str
    email: str = ""
    #: Only a provider that actually verifies delivery may set this. It gates account LINKING:
    #: auto-linking an unverified email to an existing account is account takeover.
    email_verified: bool = False
    amr: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class IdentityProvider(Protocol):
    """Verifies credentials and produces assertions. One configured per deployment."""

    #: Matches the ``provider`` column of an identity record.
    name: str
    #: Whether this provider can take an email+password at all. False for pure OIDC, which is
    #: what the sign-in UI reads to decide whether to render the password form.
    supports_password: bool

    def authenticate(self, *, email: str, password: str) -> Assertion:
        """Check a password. Raises ``AuthenticationFailed`` — and raises the SAME error for an
        unknown account as for a wrong password, so the endpoint cannot be used to enumerate who
        has signed up."""
        ...

    def register(self, *, email: str, password: str) -> Assertion:
        """Create a credential for a new account and assert it, so signup and login return the
        same shape and the caller has one path to issue tokens on."""
        ...

    def from_external_assertion(self, *, raw: str) -> Assertion:
        """P4: validate an OIDC id_token / authorization-code exchange result and assert it.
        Raises ``IdentityConfigurationError`` on a provider that has no external flow."""
        ...
