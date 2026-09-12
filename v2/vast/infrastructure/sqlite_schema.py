"""This module's own tables, on SQLite — with its own version ledger.

WHY VAST COUNTS ITS OWN VERSIONS. Adding a step to the accounts service's schema would make every
deployment's migration state depend on whether GPU renting exists, and would put this module's
tables inside a file that knows nothing about it. Identity and payments each keep their own
ledger for the same reason, and this follows them: the module is the unit that can be added,
upgraded or removed without the host tracking it.
"""

from __future__ import annotations

import sqlite3

#: Bump when adding a step to _STEPS.
SCHEMA_VERSION = 2

_STEPS: dict[int, str] = {
    2: """
    -- The per-rental secret that opens the machine (WEB_PASSWORD at launch, Bearer token in
    -- use). A column rather than a derivation from a server secret, so rotating that secret
    -- cannot lock the platform out of every machine it is currently paying for.
    ALTER TABLE vast_instances ADD COLUMN auth_token TEXT NOT NULL DEFAULT '';
    """,
    1: """
    -- One row per GPU rented on a user's behalf.
    --
    -- THE PARTIAL UNIQUE INDEX BELOW IS THE FEATURE, not an optimisation. "every user gets one
    -- machine, and every new chat finds that same machine" is enforced in the database because
    -- two chats opening in the same second is the ordinary case, not the rare one.
    -- Check-then-insert in application code has a window between the check and the insert, and
    -- a GPU rented inside that window is a second bill nobody asked for.
    --
    -- A ROW OUTLIVES ITS INSTANCE. Dead rows are kept, never deleted: `dead_reason` is the only
    -- record of WHY a machine went away (idle, orphaned, asked for), and that is the first
    -- question anyone has after a surprising invoice.
    --
    -- NO FOREIGN KEY TO accounts(id) — deliberately. This module is a guest in whatever database
    -- it is installed into and does not assume the host's table names, which is what lets it be
    -- added or removed without touching the host's schema.
    CREATE TABLE IF NOT EXISTS vast_instances (
        id           TEXT PRIMARY KEY,          -- ours; also the marketplace label, the join key
        account_id   TEXT NOT NULL,
        instance_id  INTEGER,                   -- the marketplace's id; NULL until it answers
        machine_id   INTEGER,
        url          TEXT NOT NULL DEFAULT '',  -- empty until it answers on an address
        state        TEXT NOT NULL,             -- starting | running | dead
        hourly_usd   REAL NOT NULL DEFAULT 0,
        created_at   REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        lease_until  REAL NOT NULL DEFAULT 0,   -- held across a long render
        dead_at      REAL,
        dead_reason  TEXT NOT NULL DEFAULT ''
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_vast_live_per_account
        ON vast_instances (account_id) WHERE state IN ('starting', 'running');
    -- The reaper's sweep: everything live, longest out of contact first.
    CREATE INDEX IF NOT EXISTS ix_vast_state_seen ON vast_instances (state, last_seen_at);

    -- WHEN THE REAPER LAST RAN. One row, overwritten each sweep.
    --
    -- This exists because "nothing needed reaping" and "the reaper has not run since Tuesday"
    -- are indistinguishable on every other signal — both show zero kills. A reaper that has
    -- silently stopped is the failure that costs the most, so its liveness is recorded
    -- explicitly and an alarm watches this value go stale.
    CREATE TABLE IF NOT EXISTS vast_reaper_state (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        last_sweep_at REAL NOT NULL
    );
    """,
}


def create_schema(conn: sqlite3.Connection) -> int:
    """Bring this module's tables up to ``SCHEMA_VERSION``. Idempotent; returns the version.

    Takes a LIVE CONNECTION rather than opening its own, exactly like identity's — the caller
    owns the transaction, so schema setup joins the unit of work it is already in.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vast_schema_version ("
        "  id INTEGER PRIMARY KEY CHECK (id = 1),"
        "  version INTEGER NOT NULL"
        ")"
    )
    row = conn.execute("SELECT version FROM vast_schema_version WHERE id = 1").fetchone()
    current = int(row[0]) if row else 0
    for step in range(current + 1, SCHEMA_VERSION + 1):
        script = _STEPS.get(step)
        if script:
            conn.executescript(script)
    if current != SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO vast_schema_version (id, version) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET version = excluded.version",
            (SCHEMA_VERSION,),
        )
    return SCHEMA_VERSION
