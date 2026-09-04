"""Postgres/SQLite PARITY — the gate on the migration off SQLite.

WHY THIS EXISTS AND UNIT TESTS DO NOT COVER IT. The risk in porting a storage adapter is never
"does this function run"; it is "does the new backend answer the same way as the old one". That
question cannot be asked of a mock, and it cannot be asked of one backend in isolation. So every
test here runs the SAME sequence of operations against BOTH adapters and asserts the same
outcome — if a port changed behaviour, one of the two parametrisations fails and names itself.

WHAT IT IS ACTUALLY DEFENDING. The seams, not the statements: transaction boundaries, NULL
versus 0, `rowcount` semantics, `lastrowid` versus `RETURNING`, integer/boolean coercion, and
the conflict targets that make double-charging and webhook replay impossible. Those all look
fine in review and differ at runtime.

REQUIRES A REAL POSTGRES: set `DATABASE_URL`. Without it the Postgres parametrisations skip and
the SQLite ones still run, so a developer with no database still gets the suite — but CI and the
migration gate must set it, because a skipped parity test proves nothing.

Each Postgres test gets its OWN SCHEMA, created and dropped around it, so a failure cannot leave
state that makes the next run pass or fail for the wrong reason.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid

import pytest

from identity.domain.errors import RefreshReuseDetected, TokenInvalid
from identity.infrastructure import postgres_schema, sqlite_schema
from identity.infrastructure.postgres_identity_link_store import PostgresIdentityLinkStore
from identity.infrastructure.postgres_key_store import PostgresKeyStore
from identity.infrastructure.postgres_refresh_store import PostgresRefreshStore
from identity.infrastructure.sqlite_identity_link_store import SqliteIdentityLinkStore
from identity.infrastructure.sqlite_key_store import SqliteKeyStore
from identity.infrastructure.sqlite_refresh_store import SqliteRefreshStore
from payments.domain import payment_status
from payments.domain.money import Money
from payments.domain.payment_intent import PURCHASE, PaymentIntent
from payments.infrastructure.postgres_payment_intent_store import PostgresPaymentIntentStore
from payments.infrastructure.sqlite_payment_intent_store import SqlitePaymentIntentStore

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()

#: The adapters, paired. Adding a ported store means adding one row here and nothing else.
STORES = {
    "payment_intents": (SqlitePaymentIntentStore, PostgresPaymentIntentStore),
    "identity_links": (SqliteIdentityLinkStore, PostgresIdentityLinkStore),
    "refresh": (SqliteRefreshStore, PostgresRefreshStore),
    "keys": (SqliteKeyStore, PostgresKeyStore),
}

_pg_missing = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL is not set; parity needs a real Postgres"
)

BACKENDS = ["sqlite", pytest.param("postgres", marks=_pg_missing)]


class _Db:
    """One backend's connection plus the adapter classes that belong to it, so a test body can
    be written once and run twice without naming either backend."""

    def __init__(self, conn, kind: str) -> None:
        self.conn = conn
        self.kind = kind

    def store(self, name: str, *args, **kw):
        sqlite_cls, postgres_cls = STORES[name]
        cls = sqlite_cls if self.kind == "sqlite" else postgres_cls
        return cls(self.conn, *args, **kw)

    def schema(self, name: str) -> None:
        sqlite_cls, postgres_cls = STORES[name]
        (sqlite_cls if self.kind == "sqlite" else postgres_cls).create_schema(self.conn)


@pytest.fixture(params=BACKENDS)
def db(request):
    """A connection on each backend, with identity's and payments' schemas applied.

    The stub `accounts` table exists because identity's foreign keys are real on both backends;
    the accounts core is the last thing to be ported, so until then its table is supplied here
    rather than pretended away by dropping the constraint.
    """
    if request.param == "sqlite":
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("INSERT INTO accounts (id, email) VALUES ('acct-1', 'a@x.dev')")
        sqlite_schema.create_schema(conn)
        handle = _Db(conn, "sqlite")
        handle.schema("payment_intents")
        yield handle
        conn.close()
        return

    import psycopg
    from psycopg.rows import dict_row

    schema = f"parity_{uuid.uuid4().hex[:12]}"
    conn = psycopg.connect(
        DATABASE_URL, prepare_threshold=None, autocommit=False, row_factory=dict_row
    )
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        conn.commit()
        conn.execute(
            "CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("INSERT INTO accounts (id, email) VALUES ('acct-1', 'a@x.dev')")
        postgres_schema.create_schema(conn)
        conn.commit()
        handle = _Db(conn, "postgres")
        handle.schema("payment_intents")
        conn.commit()
        yield handle
    finally:
        try:
            conn.rollback()
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.commit()
        finally:
            conn.close()


def _intent(**kw) -> PaymentIntent:
    return PaymentIntent(
        kind=PURCHASE,
        provider=kw.pop("provider", "dodo"),
        reference=kw.pop("reference", "pay_1"),
        amount=kw.pop("amount", Money.from_usd(20.0)),
        status=kw.pop("status", payment_status.SUCCEEDED),
        account_id=kw.pop("account_id", "acct-1"),
        **kw,
    )


# --- payments ----------------------------------------------------------------------------


def test_a_replayed_purchase_records_one_row(db):
    """The property that stops a double-click becoming a double charge. It is enforced by a
    UNIQUE INDEX, so it can only be verified against a real database — and on Postgres the
    partial index and the ON CONFLICT target must agree exactly or this raises instead."""
    store = db.store("payment_intents")
    intent = _intent(idempotency_key="click-1")
    store.record(intent, at=time.time())
    store.record(intent, at=time.time())
    db.conn.commit()

    rows = db.conn.execute(
        "SELECT count(*) AS n FROM payment_intents WHERE idempotency_key = 'click-1'"
    ).fetchone()
    assert rows["n"] == 1


def test_attempts_without_an_idempotency_key_are_all_kept(db):
    """Most rows carry no key, and they must NOT collide with each other — which is why the
    unique index is partial. A non-partial index would silently discard declines."""
    store = db.store("payment_intents")
    store.record(_intent(idempotency_key="", status=payment_status.FAILED), at=time.time())
    store.record(_intent(idempotency_key="", status=payment_status.FAILED), at=time.time())
    db.conn.commit()

    row = db.conn.execute(
        "SELECT count(*) AS n FROM payment_intents WHERE idempotency_key = ''"
    ).fetchone()
    assert row["n"] == 2


def test_a_redelivered_webhook_is_claimed_once(db):
    """`claim_event` is insert-and-test in one statement so two concurrent deliveries cannot
    both win. Parity here is really parity of `rowcount` after ON CONFLICT DO NOTHING."""
    store = db.store("payment_intents")
    event = f"evt-{uuid.uuid4().hex[:8]}"
    assert store.claim_event(event, at=time.time()) is True
    assert store.claim_event(event, at=time.time()) is False
    db.conn.commit()


def test_the_recorded_amount_survives_the_round_trip(db):
    """Money in, same money out — the check that a float column swap did not quietly round."""
    store = db.store("payment_intents")
    store.record(_intent(idempotency_key="amt-1", amount=Money.from_usd(20.0)), at=time.time())
    db.conn.commit()
    row = db.conn.execute(
        "SELECT amount_usd, currency FROM payment_intents WHERE idempotency_key = 'amt-1'"
    ).fetchone()
    assert float(row["amount_usd"]) == 20.0
    assert row["currency"] == "usd"


# --- identity links ----------------------------------------------------------------------


def test_relinking_updates_the_email_but_never_the_account(db):
    """Re-pointing an existing identity at a different account is what an account-takeover bug
    looks like. The UPSERT must update the asserted email and leave account_id alone."""
    links = db.store("identity_links")
    links.link(provider="local", subject="s1", account_id="acct-1", email="old@x.dev")
    links.link(
        provider="local", subject="s1", account_id="acct-EVIL",
        email="new@x.dev", email_verified=True,
    )
    db.conn.commit()

    found = links.find("local", "s1")
    assert found.account_id == "acct-1"
    assert found.email == "new@x.dev"
    assert found.email_verified is True


def test_unlink_reports_whether_it_removed_anything(db):
    links = db.store("identity_links")
    links.link(provider="local", subject="s2", account_id="acct-1")
    db.conn.commit()
    assert links.unlink("local", "s2") is True
    assert links.unlink("local", "s2") is False
    db.conn.commit()


# --- refresh tokens ----------------------------------------------------------------------


def test_issue_returns_a_usable_row_id(db):
    """`row_id` becomes `parent_id` on the next rotation, so a wrong value breaks the family
    chain silently. sqlite3 reads it from `lastrowid`, psycopg from `RETURNING id`."""
    refresh = db.store("refresh")
    _token, record = refresh.issue(account_id="acct-1", client_id="cli", device_label="laptop")
    db.conn.commit()
    assert record.row_id > 0


def test_a_reused_refresh_token_is_reported_with_its_family(db):
    """Single-use is the whole security model: the second presentation must raise the reuse
    error carrying the family id, so AuthService can revoke the family rather than guess."""
    refresh = db.store("refresh")
    token, record = refresh.issue(account_id="acct-1")
    db.conn.commit()
    refresh.consume(token)
    db.conn.commit()

    with pytest.raises(RefreshReuseDetected) as caught:
        refresh.consume(token)
    assert getattr(caught.value, "family_id", "") == record.family_id


def test_a_rotation_inherits_the_family_deadline(db):
    """The absolute lifetime is set once by the family's first token. If a rotation recomputed
    it, a session that refreshes daily would never end."""
    refresh = db.store("refresh")
    _t1, first = refresh.issue(account_id="acct-1")
    db.conn.commit()
    _t2, second = refresh.issue(
        account_id="acct-1", family_id=first.family_id, parent_row_id=first.row_id
    )
    db.conn.commit()
    assert second.family_expires_at == pytest.approx(first.family_expires_at, abs=0.001)


def test_revoking_a_family_refuses_its_tokens(db):
    refresh = db.store("refresh")
    token, record = refresh.issue(account_id="acct-1")
    db.conn.commit()
    assert refresh.revoke_family(record.family_id) >= 1
    db.conn.commit()
    with pytest.raises(TokenInvalid):
        refresh.consume(token)


def test_listing_devices_shows_one_row_per_family(db):
    """A family is a device. Listing every rotation would show a hundred entries for one laptop."""
    refresh = db.store("refresh")
    _t, first = refresh.issue(account_id="acct-1")
    db.conn.commit()
    refresh.issue(account_id="acct-1", family_id=first.family_id, parent_row_id=first.row_id)
    refresh.issue(account_id="acct-1")  # a second device
    db.conn.commit()
    assert len(refresh.list_families("acct-1")) == 2


# --- signing keys ------------------------------------------------------------------------


def test_a_fresh_deployment_mints_its_own_signing_key(db):
    """No provisioning step: a service that cannot issue a token until an operator runs one is
    a service that is down on day one."""
    keys = db.store("keys")
    key = keys.active()
    db.conn.commit()
    assert key.kid and key.private_pem
    assert keys.active().kid == key.kid  # and does not mint a second one


def test_rotation_keeps_the_old_key_verifiable(db):
    """The overlap window is what stops a token minted a second before a rotation from dying
    before it expires."""
    keys = db.store("keys")
    old = keys.active()
    db.conn.commit()
    new = keys.rotate(retire_after_s=3600)
    db.conn.commit()

    assert new.kid != old.kid
    kids = {k.kid for k in keys.verification_keys()}
    assert old.kid in kids and new.kid in kids


def test_verification_keys_never_carry_the_private_half(db):
    """JWKS is public. A private half reaching this list is a total compromise."""
    keys = db.store("keys")
    keys.active()
    db.conn.commit()
    assert all(k.private_pem == "" for k in keys.verification_keys())


# --- the dialect shim --------------------------------------------------------------------
#
# `SqliteDialectConnection` lets ~298 placeholders of BUSINESS logic (app.py, orgs_api.py,
# admin_api.py, ledger.py) run unmodified on Postgres. It is the single riskiest piece of the
# migration precisely because it is invisible: a mistranslation does not look like a bug in the
# shim, it looks like a bug in the money code. These run its output against the real database.


@_pg_missing
def test_the_shim_runs_sqlite_dialect_sql_on_postgres():
    """The base case: `?` placeholders, against Postgres, through the wrapper the service uses."""
    import psycopg
    from psycopg.rows import dict_row

    from accounts.sqlite_dialect_connection import SqliteDialectConnection

    schema = f"shim_{uuid.uuid4().hex[:12]}"
    raw = psycopg.connect(
        DATABASE_URL, prepare_threshold=None, autocommit=False, row_factory=dict_row
    )
    try:
        raw.execute(f'CREATE SCHEMA "{schema}"')
        raw.execute(f'SET search_path TO "{schema}"')
        raw.commit()
        c = SqliteDialectConnection(raw)

        c.executescript(
            "CREATE TABLE t (id INTEGER, name TEXT, note TEXT);"
            "CREATE INDEX ix_t_name ON t (name);"
        )
        c.execute("INSERT INTO t (id, name, note) VALUES (?, ?, ?)", (1, "alice", "100% sure"))
        c.execute("INSERT INTO t (id, name, note) VALUES (?, ?, ?)", (2, "bob", "maybe"))
        c.commit()

        row = c.execute("SELECT name FROM t WHERE id = ?", (1,)).fetchone()
        assert row["name"] == "alice"

        # A LIKE whose wildcard arrives IN THE PARAMETER — admin_api's account search does
        # exactly this, and it is where a naive `?`->`%s` rewrite blows up.
        rows = c.execute("SELECT id FROM t WHERE name LIKE ?", ("%li%",)).fetchall()
        assert [r["id"] for r in rows] == [1]

        # A literal `%` in the SQL alongside bound parameters: must be escaped, not read as a
        # placeholder. Unescaped, psycopg raises from inside the driver and blames the caller.
        rows = c.execute(
            "SELECT id FROM t WHERE note LIKE '100\\%%' ESCAPE '\\' OR id = ?", (2,)
        ).fetchall()
        assert {r["id"] for r in rows} == {1, 2}

        # A `?` inside a STRING LITERAL is data, not a placeholder.
        row = c.execute("SELECT '?' AS q, id FROM t WHERE id = ?", (2,)).fetchone()
        assert row["q"] == "?" and row["id"] == 2

        c.executemany("INSERT INTO t (id, name, note) VALUES (?, ?, ?)",
                      [(3, "carol", ""), (4, "dave", "")])
        c.commit()
        assert c.execute("SELECT count(*) AS n FROM t").fetchone()["n"] == 4
    finally:
        try:
            raw.rollback()
            raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            raw.commit()
        finally:
            raw.close()


@_pg_missing
def test_the_accounts_schema_holds_money_too_large_for_a_32_bit_column():
    """`ledger_entries.amount_micros` MUST be BIGINT.

    SQLite's INTEGER is 64-bit; Postgres's is 32-bit and stops at 2,147,483,647 — which in
    micros is $2,147.48 in a single transaction. A straight type translation would have built a
    ledger that worked for years and then refused a large purchase with an overflow error, in
    the money path, in production. This posts a $5,000 entry: it passes on BIGINT and raises on
    INTEGER, so the column type cannot silently regress.
    """
    import psycopg
    from psycopg.rows import dict_row

    from accounts import postgres_schema as accounts_schema
    from accounts.sqlite_dialect_connection import SqliteDialectConnection

    schema = f"acct_{uuid.uuid4().hex[:12]}"
    raw = psycopg.connect(
        DATABASE_URL, prepare_threshold=None, autocommit=False, row_factory=dict_row
    )
    try:
        raw.execute(f'CREATE SCHEMA "{schema}"')
        raw.execute(f'SET search_path TO "{schema}"')
        raw.commit()

        assert accounts_schema.create_schema(raw) == accounts_schema.SCHEMA_VERSION
        raw.commit()
        assert accounts_schema.create_schema(raw) == accounts_schema.SCHEMA_VERSION  # idempotent
        raw.commit()

        # Through the shim, because this is how ledger.py will talk to it.
        c = SqliteDialectConnection(raw)
        c.execute(
            "INSERT INTO accounts (id, email, pw_salt, pw_hash, created_at) "
            "VALUES (?, ?, '', '', ?)",
            ("acct-big", "big@x.dev", time.time()),
        )
        c.execute(
            "INSERT INTO ledger_txns (txn_id, txn_type, ts, account_id) VALUES (?, ?, ?, ?)",
            ("txn-big", "purchase", time.time(), "acct-big"),
        )
        big = 5_000 * 1_000_000  # $5,000 in micros — 2.3x a 32-bit INTEGER's ceiling
        c.execute(
            "INSERT INTO ledger_entries (txn_id, txn_type, ts, account, direction, "
            "amount_micros, account_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("txn-big", "purchase", time.time(), "cash", "debit", big, "acct-big"),
        )
        c.commit()

        row = c.execute(
            "SELECT amount_micros FROM ledger_entries WHERE txn_id = ?", ("txn-big",)
        ).fetchone()
        assert int(row["amount_micros"]) == big

        # Credits are counted in whole units and land in BIGINT columns for the same reason.
        c.execute(
            "INSERT INTO credit_grants (account_id, credits, created_at) VALUES (?, ?, ?)",
            ("acct-big", 5_000_000_000, time.time()),
        )
        c.commit()
        got = c.execute(
            "SELECT credits FROM credit_grants WHERE account_id = ?", ("acct-big",)
        ).fetchone()
        assert int(got["credits"]) == 5_000_000_000
    finally:
        try:
            raw.rollback()
            raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            raw.commit()
        finally:
            raw.close()


@_pg_missing
def test_the_shim_commits_on_exit_and_rolls_back_on_error():
    """`with _db() as c:` is how every unit of work in the service is written, and sqlite3's
    context manager commits on success / rolls back on failure. Losing that would turn a raised
    exception mid-purchase into a partially applied one."""
    import psycopg
    from psycopg.rows import dict_row

    from accounts.sqlite_dialect_connection import SqliteDialectConnection

    schema = f"shim_{uuid.uuid4().hex[:12]}"
    raw = psycopg.connect(
        DATABASE_URL, prepare_threshold=None, autocommit=False, row_factory=dict_row
    )
    try:
        raw.execute(f'CREATE SCHEMA "{schema}"')
        raw.execute(f'SET search_path TO "{schema}"')
        raw.execute("CREATE TABLE t (id INTEGER)")
        raw.commit()

        with SqliteDialectConnection(raw) as c:
            c.execute("INSERT INTO t (id) VALUES (?)", (1,))
        assert raw.execute("SELECT count(*) AS n FROM t").fetchone()["n"] == 1

        with pytest.raises(RuntimeError):
            with SqliteDialectConnection(raw) as c:
                c.execute("INSERT INTO t (id) VALUES (?)", (2,))
                raise RuntimeError("boom")
        assert raw.execute("SELECT count(*) AS n FROM t").fetchone()["n"] == 1
    finally:
        try:
            raw.rollback()
            raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            raw.commit()
        finally:
            raw.close()
