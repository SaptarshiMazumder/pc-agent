"""PostgresPaymentIntentStore — the attempt log and the webhook dedupe gate, on Postgres.

The Postgres twin of `SqlitePaymentIntentStore`, satisfying the same `PaymentIntentStore`
interface and carrying the same two guarantees, so the rest of the module cannot tell which one
it is holding:

  * an attempt is RECORDED whatever the outcome, because a decline is evidence too;
  * a redelivered webhook is FREE, because the duplicate is refused by a primary key rather than
    by a read the caller might race.

IT STILL SHARES THE CALLER'S CONNECTION AND TRANSACTION. That is not an implementation detail
to tidy away later: recording a payment and posting the ledger entry it caused must commit or
roll back together, and the module's whole reason for living in-process with accounts is to keep
that a single local transaction. Constructed per request around an open connection, exactly like
the SQLite store.

THREE THINGS DIFFER FROM THE SQLITE VERSION, and they are all in the SQL rather than the shape:

  * `%s` placeholders instead of `?` (psycopg's paramstyle).
  * `BIGSERIAL` instead of `INTEGER PRIMARY KEY AUTOINCREMENT`, and `DOUBLE PRECISION` instead of
    `REAL` — SQLite's REAL is already a double, so the stored values are unchanged.
  * The additive-column migration is `ADD COLUMN IF NOT EXISTS` rather than the PRAGMA-then-ALTER
    dance, which exists only because SQLite cannot express it.

THE PARTIAL UNIQUE INDEX IS LOAD-BEARING. `ON CONFLICT (idempotency_key) WHERE idempotency_key
<> ''` only resolves if an index with exactly that predicate exists — Postgres matches the
conflict target to the index, and a mismatch is an error at execution time, not at deploy time.
Blank keys must stay non-unique (most rows have none), which is why the predicate exists at all.
"""

from __future__ import annotations

import json
from typing import Any

from payments.domain.payment_intent import PaymentIntent


class PostgresPaymentIntentStore:
    def __init__(self, connection: Any) -> None:
        self._c = connection

    @staticmethod
    def create_schema(c: Any) -> None:
        """Idempotent, and safe to run against a database that already has these tables.

        Statements are issued one at a time rather than as a script: psycopg sends a multi
        statement string as a single implicit transaction with no parameters, which works but
        reports a failure against the whole block instead of the statement that caused it.
        """
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_intents (
                id              BIGSERIAL PRIMARY KEY,
                ts              DOUBLE PRECISION NOT NULL,
                kind            TEXT NOT NULL,
                provider        TEXT NOT NULL,
                reference       TEXT NOT NULL DEFAULT '',
                account_id      TEXT NOT NULL DEFAULT '',
                amount_usd      DOUBLE PRECISION NOT NULL DEFAULT 0,
                currency        TEXT NOT NULL DEFAULT 'usd',
                status          TEXT NOT NULL DEFAULT '',
                detail          TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL DEFAULT ''
            )
            """
        )
        c.execute(
            "ALTER TABLE payment_intents ADD COLUMN IF NOT EXISTS meta TEXT NOT NULL DEFAULT ''"
        )
        # The conflict target of `record` below. Partial, so the blank keys most rows carry do
        # not collide with each other.
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_intent_idem ON payment_intents "
            "(idempotency_key) WHERE idempotency_key <> ''"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_intent_acct ON payment_intents (account_id, ts)"
        )
        # How a webhook finds the attempt it belongs to.
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_intent_ref ON payment_intents (reference) "
            "WHERE reference <> ''"
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_events (
                event_id   TEXT PRIMARY KEY,
                ts         DOUBLE PRECISION NOT NULL
            )
            """
        )

    def record(self, intent: PaymentIntent, *, at: float) -> None:
        """Persist an attempt. A duplicate idempotency key is ignored rather than raised: the
        caller is replaying a request, which is the behaviour the key exists to make safe."""
        self._c.execute(
            "INSERT INTO payment_intents (ts, kind, provider, reference, account_id, amount_usd, "
            "currency, status, detail, idempotency_key, meta) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (idempotency_key) WHERE idempotency_key <> '' DO NOTHING",
            (
                at,
                intent.kind,
                intent.provider,
                intent.reference,
                intent.account_id,
                intent.amount.to_usd(),
                intent.amount.currency,
                intent.status,
                intent.detail,
                intent.idempotency_key,
                json.dumps(intent.meta, sort_keys=True) if intent.meta else "",
            ),
        )

    def claim_event(self, event_id: str, *, at: float) -> bool:
        """Insert-and-test, in one statement, so two concurrent deliveries of the same event
        cannot both win. `rowcount` is 0 when the row was already there — psycopg reports it
        the same way sqlite3 does, so the caller's dedupe logic is unchanged."""
        cur = self._c.execute(
            "INSERT INTO payment_events (event_id, ts) VALUES (%s, %s) "
            "ON CONFLICT (event_id) DO NOTHING",
            (event_id, at),
        )
        return cur.rowcount > 0
