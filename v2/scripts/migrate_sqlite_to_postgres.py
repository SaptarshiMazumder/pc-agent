"""Copy an accounts SQLite database into the Postgres this deployment is moving to.

WHY A SCRIPT AND NOT A ONE-LINER. `pg_dump`/`.dump` cannot do this: the two schemas are not the
same text. Postgres owns identity columns, BIGINT money and partial indexes that SQLite spells
differently or not at all (see accounts/postgres_schema.py), so a SQLite dump replayed against
Postgres fails on the first CREATE and, if forced past that, silently lands money in the wrong
type. What transfers is ROWS, into a schema Postgres built itself.

THE SCHEMA IS NEVER WRITTEN HERE. `--create-schema` calls the SAME create_schema functions the
accounts service calls at boot, so there is exactly one definition of what these tables are and
this file cannot drift from it. A migration that invents its own DDL is a second schema nobody
remembers to update.

EVERYTHING ELSE IS INTROSPECTED, nothing is listed. The table set, the column set, the foreign
keys that decide insert order and the identity columns that need their sequences resynced are all
read from the live databases at run time. Add a table to the schema and this script carries it
without being told; that is the difference between a migration tool and a snapshot of one day's
schema.

THE THREE THINGS THAT ACTUALLY GO WRONG, and what is done about each:

  FOREIGN KEY ORDER      `org_members` before `orgs` fails. The order is a topological sort of
                         the real FK graph rather than a hand-kept list, because a hand-kept
                         list is right until someone adds a table.

  IDENTITY SEQUENCES     Rows are inserted with their ORIGINAL ids -- an account's ledger must
                         keep pointing at the same rows. Postgres does not advance a sequence
                         for an explicitly supplied id, so without a setval afterwards the next
                         INSERT collides on a primary key that is already taken. This is the
                         failure that shows up days later, in production, on the first write.

  COLUMN DRIFT           Only columns present in BOTH schemas are copied, and anything dropped
                         on either side is REPORTED rather than passed over -- a column silently
                         not copied is data loss that looks like success.

IT IS ONE TRANSACTION. Either the whole database arrives or none of it does; a half-copied
accounts database is worse than an empty one, because it looks usable.

IT REFUSES A NON-EMPTY TARGET unless --force. Running twice is the obvious accident, and the
second run would either duplicate every row or die halfway through on a primary key.

USAGE
    # look, change nothing
    python migrate_sqlite_to_postgres.py --source accounts.db --dry-run

    # create the schema, then copy
    python migrate_sqlite_to_postgres.py --source accounts.db --create-schema

DATABASE_URL names the target. Prefer Neon's DIRECT endpoint (the host WITHOUT `-pooler`): this
runs one long transaction, which is exactly what a transaction-mode pooler is worst at.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

REPO_V2 = Path(__file__).resolve().parent.parent


def _load_schema_modules():
    """The service's OWN DDL. Imported lazily and with the service's own sys.path shape, because
    accounts/ is a flat module directory rather than a package -- app.py does the same."""
    sys.path[:0] = [str(REPO_V2), str(REPO_V2 / "accounts")]
    import postgres_schema as accounts_schema  # noqa: PLC0415
    from identity.infrastructure import postgres_schema as identity_schema  # noqa: PLC0415
    from payments.infrastructure.postgres_payment_intent_store import (  # noqa: PLC0415
        PostgresPaymentIntentStore,
    )

    return accounts_schema, identity_schema, PostgresPaymentIntentStore


# ────────────────────────────────── introspection ──────────────────────────────────


def target_tables(conn) -> dict[str, list[str]]:
    """{table: [column, ...]} for the public schema, in ordinal order."""
    rows = conn.execute(
        """SELECT table_name, column_name
             FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position"""
    ).fetchall()
    out: dict[str, list[str]] = {}
    for table, column in rows:
        out.setdefault(table, []).append(column)
    return out


def foreign_keys(conn) -> dict[str, set[str]]:
    """{table: {table it depends on, ...}}. Self-references are dropped: a row referencing its
    own table is ordered by the data, not by the table order, and keeping it would make the
    graph falsely cyclic."""
    rows = conn.execute(
        """SELECT tc.table_name, ccu.table_name AS refs
             FROM information_schema.table_constraints tc
             JOIN information_schema.constraint_column_usage ccu
               ON tc.constraint_name = ccu.constraint_name
              AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'"""
    ).fetchall()
    deps: dict[str, set[str]] = {}
    for table, refs in rows:
        if table != refs:
            deps.setdefault(table, set()).add(refs)
    return deps


def insert_order(tables: list[str], deps: dict[str, set[str]]) -> list[str]:
    """Parents before children. Kahn's algorithm; ties broken alphabetically so two runs against
    the same schema produce the same order and a diff of two logs is readable.

    A cycle cannot be ordered, so the remainder is appended alphabetically and the caller finds
    out from the FK error rather than from a silently wrong order."""
    remaining = {t: set(deps.get(t, set())) & set(tables) for t in tables}
    ordered: list[str] = []
    while remaining:
        ready = sorted(t for t, d in remaining.items() if not d - set(ordered))
        if not ready:
            return ordered + sorted(remaining)
        ordered.extend(ready)
        for t in ready:
            remaining.pop(t)
    return ordered


def sqlite_tables(conn) -> dict[str, list[str]]:
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {n: [c[1] for c in conn.execute(f'PRAGMA table_info("{n}")')] for n in names}


def identity_columns(conn) -> list[tuple[str, str]]:
    return [
        (t, c)
        for t, c in conn.execute(
            """SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema='public' AND is_identity='YES'"""
        ).fetchall()
    ]


# ────────────────────────────────── the copy ──────────────────────────────────


def migrate(src: Path, url: str, *, create_schema: bool, dry_run: bool, force: bool) -> int:
    import psycopg  # noqa: PLC0415 - deferred so --help works without the driver
    from psycopg import sql  # noqa: PLC0415

    lite = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    lite.row_factory = sqlite3.Row
    src_tables = sqlite_tables(lite)

    with psycopg.connect(url, connect_timeout=30) as pg:
        if create_schema:
            accounts_schema, identity_schema, intents = _load_schema_modules()
            if dry_run:
                print("[dry-run] would create the schema (service DDL)")
            else:
                accounts_schema.create_schema(pg)
                intents.create_schema(pg)
                identity_schema.create_schema(pg)
                pg.commit()
                print("schema created (service DDL)")

        dst_tables = target_tables(pg)
        if not dst_tables:
            print("target has no tables — run with --create-schema", file=sys.stderr)
            return 2

        # SCHEMA VERSION TABLES ARE NEVER COPIED. `create_schema` writes its own id=1 row into
        # each, so copying SQLite's would collide on the primary key and roll back the whole
        # migration -- and if it somehow did land, it would overwrite the version Postgres just
        # recorded with the version SQLite happened to be at, which is a LIE about a schema
        # Postgres built itself. These tables describe the schema, not the data.
        shared = [
            t for t in dst_tables if t in src_tables and not t.endswith("_schema_version")
        ]
        only_src = sorted(set(src_tables) - set(dst_tables))
        only_dst = sorted(set(dst_tables) - set(src_tables))
        order = insert_order(shared, foreign_keys(pg))

        # A target that already holds rows is the "ran it twice" accident: the second run either
        # duplicates every row or dies halfway on a primary key. `order` already excludes the
        # schema-version tables, which are non-empty on a perfectly fresh database and would
        # otherwise veto every first run.
        occupied = [
            t
            for t in order
            if pg.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(t))).fetchone()
        ]
        if occupied and not force:
            print(
                f"target is NOT empty ({', '.join(occupied)}) — refusing.\n"
                "Re-run with --force only if you mean to add to what is already there.",
                file=sys.stderr,
            )
            return 3

        if only_src:
            print(f"! in SQLite but NOT in Postgres, skipped: {', '.join(only_src)}")
        if only_dst:
            print(f"  in Postgres but not in SQLite, left empty: {', '.join(only_dst)}")

        total = 0
        report: list[tuple[str, int, list[str]]] = []
        for table in order:
            cols = [c for c in dst_tables[table] if c in src_tables[table]]
            dropped = [c for c in src_tables[table] if c not in dst_tables[table]]
            if not cols:
                continue
            quoted = ", ".join(f'"{c}"' for c in cols)
            rows = lite.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
            if not rows:
                continue
            if not dry_run:
                stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(map(sql.Identifier, cols)),
                    sql.SQL(", ").join(sql.Placeholder() * len(cols)),
                )
                pg.cursor().executemany(stmt, [tuple(r) for r in rows])
            total += len(rows)
            report.append((table, len(rows), dropped))

        # Explicitly supplied ids do not advance a sequence, so the next INSERT would collide.
        # Done inside the same transaction as the rows it is derived from.
        resynced = []
        if not dry_run:
            for table, col in identity_columns(pg):
                if table not in dict((t, n) for t, n, _ in report):
                    continue
                pg.execute(
                    sql.SQL(
                        "SELECT setval(pg_get_serial_sequence({}, {}), "
                        "COALESCE((SELECT MAX({}) FROM {}), 0) + 1, false)"
                    ).format(
                        sql.Literal(table), sql.Literal(col), sql.Identifier(col),
                        sql.Identifier(table),
                    )
                )
                resynced.append(f"{table}.{col}")

        if dry_run:
            pg.rollback()
        else:
            pg.commit()

        print(f"\n{'would copy' if dry_run else 'copied'}:")
        for table, n, dropped in report:
            note = f"   (columns not in Postgres, skipped: {', '.join(dropped)})" if dropped else ""
            print(f"  {table:26s} {n:>7}{note}")
        print(f"  {'-'*26} {'-'*7}\n  {'TOTAL':26s} {total:>7} rows")
        if resynced:
            print(f"\nidentity sequences resynced: {', '.join(sorted(resynced))}")

        if not dry_run:
            bad = []
            for table, n, _ in report:
                got = pg.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                ).fetchone()[0]
                if got != n:
                    bad.append(f"{table}: expected {n}, found {got}")
            if bad:
                print("\nVERIFY FAILED:\n  " + "\n  ".join(bad), file=sys.stderr)
                return 4
            print("\nverified: every table's row count matches the source.")
    lite.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True, type=Path, help="the SQLite file to read")
    ap.add_argument("--url", default=os.environ.get("DATABASE_URL", ""), help="target [DATABASE_URL]")
    ap.add_argument("--create-schema", action="store_true", help="build the schema first")
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument("--force", action="store_true", help="proceed even if the target has rows")
    a = ap.parse_args()

    if not a.source.exists():
        print(f"no such file: {a.source}", file=sys.stderr)
        return 2
    if not a.url:
        print("no target: pass --url or set DATABASE_URL", file=sys.stderr)
        return 2
    return migrate(
        a.source, a.url, create_schema=a.create_schema, dry_run=a.dry_run, force=a.force
    )


if __name__ == "__main__":
    raise SystemExit(main())
