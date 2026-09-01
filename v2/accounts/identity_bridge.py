"""Accounts' side of the identity boundary — the ``AccountDirectory`` port, implemented.

THE ARROW POINTS THIS WAY ON PURPOSE. Identity declares what it needs to know about an account
(``identity/application/interfaces/account_directory.py``); accounts supplies it here. So identity
never writes SQL against the money service's tables, and accounts never imports a token issuer.
It is the same inversion ``payments`` already uses with ``AccountsPostProcessor``.

This file is the ONLY place the two modules meet. If you are looking for coupling between identity
and the ledger, there isn't any — the join is one string, ``account_id``.

WHY THE PASSWORD COLUMNS ARE STILL HERE. ``pw_salt``/``pw_hash`` live on the accounts row today.
Moving them into a credentials table owned by identity in the same change that introduces tokens
would be two risky migrations at once, so the local provider reads them through this adapter for
now. When they move, only this file changes.
"""

from __future__ import annotations

import secrets
import sqlite3
import time

from identity.application.interfaces.account_directory import AccountRecord
from identity.domain.errors import AuthenticationFailed


def new_account_id() -> str:
    """``acct_<16 hex>`` — the SAME shape ``/signup`` has always minted.

    Not a cosmetic detail: this string is the token's ``sub``, the key of every usage row and
    credit grant, and the name of the account's state directory. It must keep being generated the
    same way or old and new accounts stop being interchangeable.
    """
    return "acct_" + secrets.token_hex(8)


class SqliteAccountDirectory:
    """Reads and writes the ``accounts`` table on a live connection."""

    def __init__(self, conn: sqlite3.Connection, *, clock=time.time):
        self._conn = conn
        self._clock = clock

    def find_by_id(self, account_id: str) -> AccountRecord | None:
        row = self._conn.execute(
            "SELECT id, email, active, pw_salt, pw_hash FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        return self._record(row)

    def find_by_email(self, email: str) -> AccountRecord | None:
        clean = (email or "").strip().lower()
        if not clean:
            return None
        row = self._conn.execute(
            "SELECT id, email, active, pw_salt, pw_hash FROM accounts WHERE email = ?",
            (clean,),
        ).fetchone()
        return self._record(row)

    def create(self, *, email: str, password_salt: str = "", password_hash: str = "") -> AccountRecord:
        clean = (email or "").strip().lower()
        if self.find_by_email(clean) is not None:
            # Raised as an identity error, not an HTTPException: this module must stay usable
            # from a CLI and a test, and the router owns the status-code mapping.
            raise AuthenticationFailed("there is already an account with that email")
        account_id = new_account_id()
        self._conn.execute(
            "INSERT INTO accounts (id, email, pw_salt, pw_hash, budget_usd, active, created_at) "
            "VALUES (?, ?, ?, ?, NULL, 1, ?)",
            (account_id, clean, password_salt, password_hash, float(self._clock())),
        )
        return AccountRecord(
            account_id=account_id,
            email=clean,
            active=True,
            password_salt=password_salt,
            password_hash=password_hash,
        )

    def set_password(self, account_id: str, *, password_salt: str, password_hash: str) -> None:
        self._conn.execute(
            "UPDATE accounts SET pw_salt = ?, pw_hash = ? WHERE id = ?",
            (password_salt, password_hash, account_id),
        )

    @staticmethod
    def _record(row) -> AccountRecord | None:
        if row is None:
            return None
        return AccountRecord(
            account_id=str(row["id"]),
            email=str(row["email"] or ""),
            active=bool(row["active"]),
            password_salt=str(row["pw_salt"] or ""),
            password_hash=str(row["pw_hash"] or ""),
        )
