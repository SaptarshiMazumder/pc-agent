"""The identities table, in SQLite.

Small on purpose. The interesting decisions (why email is not the key, why linking requires a
verified address) live in the interface and in ``PrincipalService``; this is only storage.

``link`` UPSERTS rather than inserting. A provider legitimately changes the email it asserts
between logins — people rename their Google account — and a store that failed on the second login
would turn a routine attribute change into a locked-out user.
"""

from __future__ import annotations

import sqlite3
import time

from identity.application.interfaces.identity_link_store import IdentityLink


class SqliteIdentityLinkStore:
    def __init__(self, conn: sqlite3.Connection, *, clock=time.time):
        self._conn = conn
        self._clock = clock

    def find(self, provider: str, subject: str) -> IdentityLink | None:
        row = self._conn.execute(
            "SELECT * FROM identities WHERE provider = ? AND subject = ?",
            (provider, subject),
        ).fetchone()
        return self._row(row) if row is not None else None

    def link(
        self,
        *,
        provider: str,
        subject: str,
        account_id: str,
        email: str = "",
        email_verified: bool = False,
    ) -> IdentityLink:
        now = float(self._clock())
        self._conn.execute(
            "INSERT INTO identities (provider, subject, account_id, email, email_verified, linked_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, subject) DO UPDATE SET "
            # account_id is NOT updated on conflict: re-pointing an existing identity at a
            # different account is exactly what an account-takeover bug looks like, and no
            # legitimate login flow needs it. Moving one is a deliberate unlink + link.
            "  email = excluded.email, email_verified = excluded.email_verified",
            (provider, subject, account_id, email, 1 if email_verified else 0, now),
        )
        found = self.find(provider, subject)
        assert found is not None  # just written
        return found

    def for_account(self, account_id: str) -> list[IdentityLink]:
        rows = self._conn.execute(
            "SELECT * FROM identities WHERE account_id = ? ORDER BY linked_at ASC",
            (account_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def unlink(self, provider: str, subject: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM identities WHERE provider = ? AND subject = ?", (provider, subject)
        )
        return bool(cur.rowcount)

    @staticmethod
    def _row(row) -> IdentityLink:
        return IdentityLink(
            provider=str(row["provider"]),
            subject=str(row["subject"]),
            account_id=str(row["account_id"]),
            email=str(row["email"] or ""),
            email_verified=bool(row["email_verified"]),
            linked_at=float(row["linked_at"] or 0),
        )
