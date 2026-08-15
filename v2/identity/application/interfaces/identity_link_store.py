"""IdentityLinkStore — the table that makes "one account, several logins" possible.

A row says: *provider P's user S is our account A*. That indirection is the entire reason adding
Google later is a row and a button rather than a migration.

WHY NOT JUST MATCH ON EMAIL. Because email is not an identifier: users change it, providers
reassign it, and an unverified one is simply a string the caller typed. Auto-linking a login to an
existing account because the addresses match is account takeover whenever the provider did not
actually verify delivery — so linking requires ``email_verified`` from the provider, and otherwise
a deliberate, authenticated link action by the user.

The local provider gets a row too, with ``subject`` = the account id. That is not a special case
worth avoiding: it means the login path has ONE shape, and the day the local provider is retired
in favour of Cognito, nothing about the lookup changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class IdentityLink:
    provider: str
    subject: str
    account_id: str
    email: str = ""
    email_verified: bool = False
    linked_at: float = 0.0


@runtime_checkable
class IdentityLinkStore(Protocol):
    def find(self, provider: str, subject: str) -> IdentityLink | None:
        """The lookup every login performs. Unique on ``(provider, subject)``."""
        ...

    def link(
        self,
        *,
        provider: str,
        subject: str,
        account_id: str,
        email: str = "",
        email_verified: bool = False,
    ) -> IdentityLink:
        """Attach a provider identity to an account. Idempotent: re-linking the same pair updates
        the email/verified attributes rather than failing, because a provider legitimately changes
        those between logins."""
        ...

    def for_account(self, account_id: str) -> list[IdentityLink]:
        """Every login attached to an account — what a "connected accounts" screen renders, and
        what an unlink action must consult so it cannot remove the LAST way in."""
        ...

    def unlink(self, provider: str, subject: str) -> bool: ...
