"""SqlitePaymentIntentStore — the attempt log and the webhook dedupe gate, on the accounts DB.

WHY IT SHARES THE ACCOUNTS DATABASE rather than owning one. Recording a payment and posting the
ledger entry it caused must commit or roll back together. Two databases turn that into a
distributed transaction, and the failure it introduces — money recorded as taken with no credits
granted — is exactly the failure this whole module exists to prevent. Sharing a connection is the
cheap way to get atomicity; the module still never reads an accounts table.

CONSTRUCTED PER REQUEST, around the caller's open connection, so the writes land inside the
caller's transaction and not in one of their own.

THE TABLE IS UNCHANGED from `accounts/payments.py`, deliberately: the dev database already has
rows in it, and a money table is the last place to want a rewrite. `meta` is added additively.
"""

from __future__ import annotations

import json
import sqlite3

from payments.domain.payment_intent import PaymentIntent


class SqlitePaymentIntentStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._c = connection

    @staticmethod
    def create_schema(c: sqlite3.Connection) -> None:
        """Idempotent, and additive over the pre-module shape.

        `payment_intents` records what we ASKED a third party to do; the ledger records what is
        TRUE about our books. They are separate on purpose — when they disagree, that gap is what
        reconciliation looks for, and it only exists if both sides were written down.

        `payment_events` exists so a redelivered webhook is free. Its PRIMARY KEY is the gate:
        the duplicate is rejected by the database, not by a read the caller might race.
        """
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS payment_intents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              REAL NOT NULL,
                kind            TEXT NOT NULL,            -- purchase | refund | payout
                provider        TEXT NOT NULL,
                reference       TEXT NOT NULL DEFAULT '',
                account_id      TEXT NOT NULL DEFAULT '',
                amount_usd      REAL NOT NULL DEFAULT 0,
                currency        TEXT NOT NULL DEFAULT 'usd',
                status          TEXT NOT NULL DEFAULT '',
                detail          TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ix_intent_idem
                ON payment_intents(idempotency_key) WHERE idempotency_key <> '';
            CREATE INDEX IF NOT EXISTS ix_intent_acct ON payment_intents(account_id, ts);

            CREATE TABLE IF NOT EXISTS payment_events (
                event_id   TEXT PRIMARY KEY,
                ts         REAL NOT NULL
            );
            """
        )
        # Additive columns, for databases created before this module existed. Same PRAGMA-then-
        # ALTER shape the accounts service uses for its own migrations.
        existing = {row[1] for row in c.execute("PRAGMA table_info(payment_intents)")}
        if "meta" not in existing:
            c.execute("ALTER TABLE payment_intents ADD COLUMN meta TEXT NOT NULL DEFAULT ''")
        # A reference lookup is how a webhook finds the attempt it belongs to.
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_intent_ref "
            "ON payment_intents(reference) WHERE reference <> ''"
        )

    def record(self, intent: PaymentIntent, *, at: float) -> None:
        """Persist an attempt. A duplicate idempotency key is ignored rather than raised: the
        caller is replaying a request, which is the behaviour the key exists to make safe."""
        self._c.execute(
            "INSERT INTO payment_intents (ts, kind, provider, reference, account_id, amount_usd, "
            "currency, status, detail, idempotency_key, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(idempotency_key) WHERE idempotency_key <> '' DO NOTHING",
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
        cannot both win. `rowcount` is 0 when the row was already there."""
        cur = self._c.execute(
            "INSERT INTO payment_events (event_id, ts) VALUES (?, ?) "
            "ON CONFLICT(event_id) DO NOTHING",
            (event_id, at),
        )
        return cur.rowcount > 0
