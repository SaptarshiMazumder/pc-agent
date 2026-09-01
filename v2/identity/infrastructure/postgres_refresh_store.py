"""Refresh tokens in Postgres: hashed at rest, single-use, family-revocable.

The Postgres twin of `SqliteRefreshStore`, satisfying the same interface and preserving every
security property of it:

  * THE PLAINTEXT EXISTS FOR ONE FUNCTION CALL. ``issue`` mints it, returns it, and stores only
    ``sha256(token)``. No code path can recover a refresh token from the database, which is what
    makes a leaked backup survivable.
  * SHA-256 AND NOT PBKDF2, deliberately: a refresh token is 32 bytes from ``secrets``, so there
    is nothing for a slow hash to defend against, and 200k rounds would sit on the client's
    critical path buying nothing.
  * ``consume`` DOES NOT REVOKE ON REUSE. It reports the reuse and lets ``AuthService`` decide,
    so the "theft means kill the family" policy stays in one readable place.

THE ONE STRUCTURAL DIFFERENCE IS `lastrowid`. sqlite3 hands back the inserted row id on the
cursor; psycopg has no equivalent, so the INSERT carries `RETURNING id` and reads it. That id
becomes `RefreshRecord.row_id`, which rotation uses as `parent_id` — so getting it wrong would
silently break the token family chain rather than raise.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Any

from identity.domain.errors import RefreshReuseDetected, TokenInvalid
from identity.domain.token import RefreshRecord

TOKEN_PREFIX = "rt_"

DEFAULT_TTL_S = 30 * 86_400  # sliding, per token
DEFAULT_FAMILY_TTL_S = 90 * 86_400  # absolute, per login


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


class PostgresRefreshStore:
    def __init__(
        self,
        conn: Any,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        family_ttl_s: float = DEFAULT_FAMILY_TTL_S,
        clock=time.time,
    ):
        self._conn = conn
        self._ttl = float(ttl_s)
        self._family_ttl = float(family_ttl_s)
        self._clock = clock

    def issue(
        self,
        *,
        account_id: str,
        family_id: str = "",
        parent_row_id: int | None = None,
        client_id: str = "",
        device_label: str = "",
    ) -> tuple[str, RefreshRecord]:
        now = float(self._clock())
        family = family_id or uuid.uuid4().hex
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)

        # The family's absolute deadline is set ONCE, by its first token, and inherited by every
        # rotation. Recomputing it per rotation would make the "absolute" lifetime slide too, and
        # a session that refreshes daily would never end.
        family_expires = now + self._family_ttl
        if family_id:
            row = self._conn.execute(
                "SELECT family_expires_at FROM refresh_tokens WHERE family_id = %s "
                "ORDER BY id ASC LIMIT 1",
                (family,),
            ).fetchone()
            if row is not None and row["family_expires_at"]:
                family_expires = float(row["family_expires_at"])

        expires = min(now + self._ttl, family_expires)
        row = self._conn.execute(
            "INSERT INTO refresh_tokens (token_hash, account_id, family_id, parent_id, client_id, "
            "device_label, issued_at, expires_at, family_expires_at, used_at, revoked_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0) RETURNING id",
            (
                _hash(token),
                account_id,
                family,
                parent_row_id,
                client_id[:64],
                device_label[:120],
                now,
                expires,
                family_expires,
            ),
        ).fetchone()
        record = RefreshRecord(
            row_id=int(row["id"] if row else 0),
            account_id=account_id,
            family_id=family,
            client_id=client_id,
            device_label=device_label,
            issued_at=now,
            expires_at=expires,
            family_expires_at=family_expires,
        )
        return token, record

    def consume(self, token: str) -> RefreshRecord:
        now = float(self._clock())
        row = self._conn.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = %s", (_hash(token),)
        ).fetchone()
        if row is None:
            raise TokenInvalid("unknown refresh token")

        record = self._row_to_record(row)
        if row["revoked_at"]:
            raise TokenInvalid("this session was signed out")
        if row["used_at"]:
            # Already rotated. Two parties hold this value; AuthService kills the family. The
            # family id rides on the exception so the caller does not have to re-query for it.
            reuse = RefreshReuseDetected("refresh token reuse detected")
            reuse.family_id = record.family_id  # type: ignore[attr-defined]
            raise reuse
        if row["expires_at"] and now > float(row["expires_at"]):
            raise TokenInvalid("refresh token expired")
        if row["family_expires_at"] and now > float(row["family_expires_at"]):
            raise TokenInvalid("session expired — please sign in again")

        self._conn.execute(
            "UPDATE refresh_tokens SET used_at = %s WHERE id = %s", (now, row["id"])
        )
        return record

    def revoke_family(self, family_id: str) -> int:
        now = float(self._clock())
        cur = self._conn.execute(
            "UPDATE refresh_tokens SET revoked_at = %s WHERE family_id = %s AND revoked_at = 0",
            (now, family_id),
        )
        return int(cur.rowcount or 0)

    def revoke_account(self, account_id: str) -> int:
        now = float(self._clock())
        cur = self._conn.execute(
            "UPDATE refresh_tokens SET revoked_at = %s WHERE account_id = %s AND revoked_at = 0",
            (now, account_id),
        )
        return int(cur.rowcount or 0)

    def list_families(self, account_id: str) -> list[RefreshRecord]:
        now = float(self._clock())
        # ONE ROW PER FAMILY — the newest live token in each. A family is a device; listing every
        # rotation would show a "devices" screen with a hundred entries for one laptop.
        rows = self._conn.execute(
            "SELECT * FROM refresh_tokens t WHERE t.account_id = %s AND t.revoked_at = 0 "
            "AND t.used_at = 0 AND (t.expires_at = 0 OR t.expires_at > %s) "
            "AND t.id = (SELECT MAX(id) FROM refresh_tokens x WHERE x.family_id = t.family_id) "
            "ORDER BY t.issued_at DESC",
            (account_id, now),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def purge_expired(self, *, before: float = 0.0) -> int:
        cutoff = before or float(self._clock())
        cur = self._conn.execute(
            "DELETE FROM refresh_tokens WHERE (expires_at > 0 AND expires_at < %s) "
            "OR (family_expires_at > 0 AND family_expires_at < %s)",
            (cutoff, cutoff),
        )
        return int(cur.rowcount or 0)

    @staticmethod
    def _row_to_record(row) -> RefreshRecord:
        return RefreshRecord(
            row_id=int(row["id"]),
            account_id=str(row["account_id"]),
            family_id=str(row["family_id"]),
            client_id=str(row["client_id"] or ""),
            device_label=str(row["device_label"] or ""),
            issued_at=float(row["issued_at"] or 0),
            expires_at=float(row["expires_at"] or 0),
            family_expires_at=float(row["family_expires_at"] or 0),
        )
