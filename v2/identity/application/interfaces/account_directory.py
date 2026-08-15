"""AccountDirectory — the ONE place identity is allowed to touch the accounts table.

THIS IS THE INVERTED DEPENDENCY, and it is the whole reason the two modules can share a database
without sharing a codebase. A brand-new user (today: a signup; at P4: a first Google login) has no
``accounts`` row yet, so identity must be able to create one. It must NOT do that with its own SQL,
because then identity would know the shape of the money service's tables and the split would be
decorative.

So identity DECLARES this port and accounts IMPLEMENTS it (``accounts/identity_bridge.py``). The
arrow points from accounts to identity — the same direction ``payments`` already points with
``AccountsPostProcessor``. Identity never imports ``ledger``; accounts never imports a token issuer.

THE PASSWORD COLUMNS ARE HERE FOR ONE PHASE ONLY. ``pw_salt``/``pw_hash`` live on the accounts
table today, and moving them in the same change that introduces tokens would mean two risky things
at once. The local provider reads them through this port; when they migrate into a credentials
table owned by identity, this interface loses two fields and nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AccountRecord:
    """An accounts row, as identity is allowed to see one. No budget, no credits, no ledger —
    identity has no business knowing an account has money at all."""

    account_id: str
    email: str
    active: bool = True
    #: Present only while the local password provider still reads these columns (see module note).
    password_salt: str = ""
    password_hash: str = ""


@runtime_checkable
class AccountDirectory(Protocol):
    """Look up and create accounts. Implemented by the accounts service."""

    def find_by_id(self, account_id: str) -> AccountRecord | None: ...

    def find_by_email(self, email: str) -> AccountRecord | None:
        """Email is looked up NORMALISED (stripped + lower-cased) by the caller; implementations
        must not apply a second, different normalisation or the two will disagree on which address
        is which."""
        ...

    def create(self, *, email: str, password_salt: str = "", password_hash: str = "") -> AccountRecord:
        """Mint a new account. Raises on a duplicate email — the caller turns that into a 409.

        Empty credentials are legal and are what an external-provider signup passes: there is no
        password to store, and inventing one would create a second way into the account that the
        user never chose.
        """
        ...

    def set_password(self, account_id: str, *, password_salt: str, password_hash: str) -> None:
        """Replace the stored password (a reset, or an on-login hash upgrade)."""
        ...
