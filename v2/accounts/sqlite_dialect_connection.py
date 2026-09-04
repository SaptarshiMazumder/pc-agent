"""SqliteDialectConnection — a Postgres connection that accepts SQLite-dialect SQL.

WHY THIS EXISTS, AND WHY IT IS NOT A BAND-AID. The accounts service holds ~298 parameter
placeholders spread through routers and services — `app.py`, `orgs_api.py`, `admin_api.py`,
`ledger.py`. Those files are BUSINESS LOGIC, not storage adapters: they decide what a purchase
means and who may spend an organisation's credits. There were two ways to run them on Postgres:

  1. Hand-edit 298 placeholders across the money code — 298 independent chances to make a silent
     mistake in the one part of the system where a silent mistake is unrecoverable; or
  2. Write the translation ONCE, test it once, and leave the business logic untouched.

This is (2). The stores that are genuinely storage — the payment intents, identity's three —
were ported properly to native `%s` adapters, because there the SQL *is* the module. This shim
is for the code where the SQL is incidental to the logic around it.

IT HAS A DEFINED END. When the cutover is done and SQLite is deleted, this class can be removed
by rewriting each statement with `translate()` itself and pasting the result back — a mechanical
transformation verified by the same tests that guard it now. It is a migration tool with an exit,
not a permanent layer.

THE TRANSLATION IS A TOKENIZER, NOT A REGEX, and that distinction is the whole correctness story:

  * `?` becomes `%s` — but ONLY outside string literals and comments. `WHERE name = '?'` must
    survive intact, and a `?` in a `--` comment must not become a phantom parameter.
  * A literal `%` becomes `%%` WHEN PARAMETERS ARE BOUND, because psycopg reads `%` as the start
    of a placeholder. This is the one that would have bitten silently: `LIKE '%foo%'` with a
    parameter raises `IndexError: tuple index out of range` from inside the driver, which reads
    like a caller bug and is not.
  * `''` inside a string literal is SQLite's escaped quote and does not end the literal.

ROWS ARE MAPPINGS on both sides — sqlite3.Row and psycopg's dict_row both index by column name —
so the calling code's `row["credits"]` works unchanged. That is configured on the pool, not here.
"""

from __future__ import annotations

from typing import Any, Iterable


def translate(sql: str, *, has_params: bool) -> str:
    """SQLite-dialect SQL -> psycopg-dialect SQL.

    `has_params` decides whether `%` needs escaping: psycopg only interprets `%` when it is
    binding parameters, and doubling it unconditionally would corrupt a parameterless statement.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    in_string = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            out.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            out.append(ch)
            if ch == "*" and nxt == "/":
                out.append(nxt)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_string:
            if ch == "'" and nxt == "'":  # SQLite's escaped quote: stays inside the literal
                out.append("''")
                i += 2
                continue
            if ch == "%" and has_params:
                out.append("%%")
                i += 1
                continue
            out.append(ch)
            if ch == "'":
                in_string = False
            i += 1
            continue

        # --- outside strings and comments ---
        if ch == "'":
            in_string = True
            out.append(ch)
        elif ch == "-" and nxt == "-":
            in_line_comment = True
            out.append("--")
            i += 2
            continue
        elif ch == "/" and nxt == "*":
            in_block_comment = True
            out.append("/*")
            i += 2
            continue
        elif ch == "?":
            out.append("%s")
        elif ch == "%" and has_params:
            out.append("%%")
        else:
            out.append(ch)
        i += 1

    return "".join(out)


class SqliteDialectConnection:
    """Wraps a psycopg connection and presents the sqlite3 surface the accounts service uses:
    `execute`, `executemany`, `executescript`, `commit`, `rollback`, `close`.

    Everything not overridden is delegated, so anything the service does that is already
    portable keeps working without this class having to know about it.
    """

    #: HOW A CALLER PICKS ITS STORES without importing this class. Identity and payments both
    #: need to know which adapter to construct around a connection, and neither may import from
    #: `accounts` — that would invert the dependency (accounts imports THEM). So the connection
    #: announces its own dialect and they read it with `getattr(conn, "dialect", "sqlite")`;
    #: a plain sqlite3.Connection has no such attribute and falls through to the default.
    dialect = "postgres"

    def __init__(self, connection: Any) -> None:
        self._c = connection

    @property
    def raw(self) -> Any:
        """The psycopg connection underneath, for adapters that speak Postgres NATIVELY.

        THE TWO MECHANISMS MUST NOT BE STACKED. A native adapter writes `%s` itself; passing it
        this wrapper would translate again — `%s` becomes `%%s` when parameters are bound, and
        psycopg then reports "the query has 0 placeholders but 2 parameters were passed", which
        names neither the wrapper nor the adapter. Unwrapping here keeps the SAME connection and
        therefore the same transaction, so atomicity across a mixed set of stores is unchanged.
        """
        return self._c

    # -- the sqlite3 surface ------------------------------------------------------------

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> Any:
        params = tuple(parameters or ())
        return self._c.execute(translate(sql, has_params=bool(params)), params or None)

    def executemany(self, sql: str, seq_of_parameters: Iterable[Iterable[Any]]) -> Any:
        rows = [tuple(p) for p in seq_of_parameters]
        if not rows:
            return None
        cur = self._c.cursor()
        cur.executemany(translate(sql, has_params=True), rows)
        return cur

    def executescript(self, script: str) -> Any:
        """sqlite3's multi-statement helper. psycopg accepts a multi-statement string as long as
        no parameters are bound — which is exactly the case a script is used for."""
        return self._c.execute(translate(script, has_params=False))

    def commit(self) -> None:
        self._c.commit()

    def rollback(self) -> None:
        self._c.rollback()

    def close(self) -> None:
        self._c.close()

    # -- everything else is the underlying connection's ---------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(self._c, name)

    def __enter__(self) -> "SqliteDialectConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # sqlite3's connection context manager commits on success and rolls back on error. The
        # accounts service relies on that shape (`with _db() as c:` around a unit of work), so it
        # is reproduced here rather than inherited from psycopg's own, which is per-transaction.
        if exc_type is None:
            self._c.commit()
        else:
            self._c.rollback()
