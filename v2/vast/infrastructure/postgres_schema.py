"""This module's own tables, on Postgres — the same shape as the SQLite ledger beside it.

TYPES ARE NOT A STRAIGHT TRANSLATION, for the reason accounts/postgres_schema.py documents at
length: SQLite INTEGER is 64-bit while Postgres INTEGER stops at 2,147,483,647. Marketplace
instance and machine ids are external identifiers that grow forever, so they are BIGINT here.
Timestamps stay epoch doubles, matching every other table in this database.
"""

from __future__ import annotations

from typing import Any

#: Bump when adding a step to _STEPS. Kept in lockstep with the SQLite ledger: one module, one
#: shape, two engines.
SCHEMA_VERSION = 2

_STEPS: dict[int, str] = {
    2: """
    -- The per-rental secret that opens the machine (WEB_PASSWORD at launch, Bearer token in
    -- use). A column rather than a derivation from a server secret, so rotating that secret
    -- cannot lock the platform out of every machine it is currently paying for.
    ALTER TABLE vast_instances ADD COLUMN IF NOT EXISTS auth_token TEXT NOT NULL DEFAULT '';
    """,
    1: """
    -- See vast/infrastructure/sqlite_schema.py for why the partial unique index is the feature
    -- rather than an optimisation, why dead rows are kept, and why there is no foreign key.
    CREATE TABLE IF NOT EXISTS vast_instances (
        id           TEXT PRIMARY KEY,
        account_id   TEXT NOT NULL,
        instance_id  BIGINT,
        machine_id   BIGINT,
        url          TEXT NOT NULL DEFAULT '',
        state        TEXT NOT NULL,
        hourly_usd   DOUBLE PRECISION NOT NULL DEFAULT 0,
        created_at   DOUBLE PRECISION NOT NULL,
        last_seen_at DOUBLE PRECISION NOT NULL,
        lease_until  DOUBLE PRECISION NOT NULL DEFAULT 0,
        dead_at      DOUBLE PRECISION,
        dead_reason  TEXT NOT NULL DEFAULT ''
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_vast_live_per_account
        ON vast_instances (account_id) WHERE state IN ('starting', 'running');
    CREATE INDEX IF NOT EXISTS ix_vast_state_seen ON vast_instances (state, last_seen_at);

    -- WHEN THE REAPER LAST RAN. One row, overwritten each sweep.
    --
    -- This exists because "nothing needed reaping" and "the reaper has not run since Tuesday"
    -- are indistinguishable on every other signal — both show zero kills. A reaper that has
    -- silently stopped is the failure that costs the most, so its liveness is recorded
    -- explicitly and an alarm watches this value go stale.
    CREATE TABLE IF NOT EXISTS vast_reaper_state (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        last_sweep_at DOUBLE PRECISION NOT NULL
    );
    """,
}


def create_schema(conn: Any) -> int:
    """Bring this module's tables up to ``SCHEMA_VERSION``. Idempotent; returns the version."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vast_schema_version ("
        "  id INTEGER PRIMARY KEY CHECK (id = 1),"
        "  version INTEGER NOT NULL"
        ")"
    )
    row = conn.execute("SELECT version FROM vast_schema_version WHERE id = 1").fetchone()
    current = int(row["version"]) if row else 0
    for step in range(current + 1, SCHEMA_VERSION + 1):
        script = _STEPS.get(step)
        if script:
            conn.execute(script)
    if current != SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO vast_schema_version (id, version) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version",
            (SCHEMA_VERSION,),
        )
    return SCHEMA_VERSION
