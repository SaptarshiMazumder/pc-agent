"""RefreshStore — rotating refresh tokens with reuse detection.

THREE RULES, AND EACH ONE IS LOAD-BEARING.

1. STORED HASHED, NEVER PLAINTEXT. A refresh token is a 30-day credential. Anyone who can read
   the database can otherwise become every user on the platform, and database backups travel.
   We hand the plaintext to the client exactly once and keep only its SHA-256.

2. SINGLE USE, ROTATED ON EVERY REFRESH. The client trades its token for a new pair each time.
   This is what bounds the damage of a token that leaks from a log or a disk image: it is useful
   only until its owner next refreshes.

3. REUSE REVOKES THE FAMILY. If a token that was already rotated is presented again, two parties
   hold it — the legitimate client rotated and forgot the old value, so the second presenter is
   a copy. We cannot tell WHICH of the two is the thief, so the only safe action is to invalidate
   the entire chain and make both sign in again. That is why rows carry a ``family_id``: it is the
   correct blast radius, wider than one token and narrower than the whole account.

A FAMILY ALSO HAS AN ABSOLUTE LIFETIME (``family_expires_at``), separate from the sliding per-token
one. Without it, a client that refreshes every day stays signed in forever, and "log everyone out"
becomes impossible to reason about.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from identity.domain.token import RefreshRecord


@runtime_checkable
class RefreshStore(Protocol):
    def issue(
        self,
        *,
        account_id: str,
        family_id: str = "",
        parent_row_id: int | None = None,
        client_id: str = "",
        device_label: str = "",
    ) -> tuple[str, RefreshRecord]:
        """Mint a refresh token. Returns ``(plaintext, record)`` — the plaintext is returned HERE
        and never again, because only its hash is stored.

        An empty ``family_id`` starts a new family (a fresh login); passing one continues an
        existing chain (a rotation).
        """
        ...

    def consume(self, token: str) -> RefreshRecord:
        """Spend a refresh token, marking it used.

        Raises ``TokenInvalid`` when it is unknown, revoked, or past either expiry, and
        ``RefreshReuseDetected`` when it was already spent — the caller must treat the latter as
        theft and revoke the family (this method does not, so the decision stays in one place and
        is visible to the reader of the service).
        """
        ...

    def revoke_family(self, family_id: str) -> int:
        """Kill one login's whole chain. Returns rows affected."""
        ...

    def revoke_account(self, account_id: str) -> int:
        """Kill every chain for an account — "sign out everywhere", and the response to a
        password change. Returns rows affected."""
        ...

    def list_families(self, account_id: str) -> list[RefreshRecord]:
        """Live families for this account, newest first — one entry per signed-in device, which
        is what a "your devices" screen renders."""
        ...

    def purge_expired(self, *, before: float = 0.0) -> int:
        """Delete rows past their expiry. Housekeeping: the table is append-heavy and nothing
        else ever removes from it."""
        ...
