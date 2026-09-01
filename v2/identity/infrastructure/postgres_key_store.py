"""Signing keys in Postgres. The Postgres twin of `SqliteKeyStore`.

THE KEY MATERIAL ITSELF IS NOT HERE. Generation and envelope encryption live in
`signing_key_material`, shared with the SQLite adapter — a key wrapped by one and unwrapped by
the other must agree byte for byte forever, and the only way to guarantee that is for there to be
one implementation. This file is storage and nothing else.

MINTING ON FIRST READ IS PRESERVED, and it is the behaviour that makes a fresh deployment work:
`active()` and `verification_keys()` create a key rather than returning nothing, because a
service that cannot issue a token until an operator runs a provisioning step is a service that
is down on day one, and an empty JWKS silently breaks every verifier.

Only the placeholders differ from the SQLite version. The `active`/`encrypted` columns stay
integers (see postgres_schema for why), so the 1/0 writes and `bool(...)` reads are unchanged.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from identity.application.interfaces.key_store import SigningKey
from identity.infrastructure.jwt_token_issuer import EDDSA
from identity.infrastructure.signing_key_material import generate as _generate
from identity.infrastructure.signing_key_material import unwrap as _unwrap
from identity.infrastructure.signing_key_material import wrap as _wrap


class PostgresKeyStore:
    """Takes a live connection, like the SQLite store — the caller owns the transaction."""

    def __init__(self, conn: Any, *, alg: str = EDDSA, clock=time.time):
        self._conn = conn
        self._alg = alg
        self._clock = clock

    def active(self) -> SigningKey:
        row = self._conn.execute(
            "SELECT * FROM signing_keys WHERE active = 1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return self._row_to_key(row, with_private=True)
        # FIRST CALL ON A FRESH DEPLOYMENT mints the key.
        return self._create(self._alg, active=True)

    def verification_keys(self) -> list[SigningKey]:
        now = float(self._clock())
        rows = self._conn.execute(
            "SELECT * FROM signing_keys WHERE active = 1 OR expires_at = 0 OR expires_at > %s "
            "ORDER BY active DESC, created_at DESC",
            (now,),
        ).fetchall()
        if not rows:
            # Serving an empty JWKS would silently break every verifier.
            return [self._create(self._alg, active=True)]
        return [self._row_to_key(r, with_private=False) for r in rows]

    def rotate(self, *, retire_after_s: float) -> SigningKey:
        now = float(self._clock())
        # The outgoing key keeps verifying for `retire_after_s`. That window MUST exceed the
        # access-token TTL or a token minted a second before the rotation dies before it expires.
        self._conn.execute(
            "UPDATE signing_keys SET active = 0, expires_at = %s WHERE active = 1",
            (now + max(0.0, float(retire_after_s)),),
        )
        return self._create(self._alg, active=True)

    # -- internals --------------------------------------------------------------------------

    def _create(self, alg: str, *, active: bool) -> SigningKey:
        private_pem, public_pem = _generate(alg)
        stored, encrypted = _wrap(private_pem)
        kid = uuid.uuid4().hex[:16]
        now = float(self._clock())
        self._conn.execute(
            "INSERT INTO signing_keys (kid, alg, public_pem, private_pem, encrypted, created_at, "
            "expires_at, active) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
            (kid, alg, public_pem, stored, 1 if encrypted else 0, now, 1 if active else 0),
        )
        return SigningKey(
            kid=kid, alg=alg, public_pem=public_pem, private_pem=private_pem, created_at=now
        )

    def _row_to_key(self, row, *, with_private: bool) -> SigningKey:
        private = ""
        if with_private:
            private = _unwrap(str(row["private_pem"]), bool(row["encrypted"]))
        return SigningKey(
            kid=str(row["kid"]),
            alg=str(row["alg"]),
            public_pem=str(row["public_pem"]),
            private_pem=private,
            created_at=float(row["created_at"] or 0),
            expires_at=float(row["expires_at"] or 0),
        )
