"""PostgresConnectionPool — the accounts service's Postgres connections, and nothing else.

WHY A POOL AT ALL. The SQLite code opens a connection per request (`_db()`), which costs
microseconds against a local file. The same shape against a hosted Postgres would pay a TLS
handshake and an authentication round trip on every request — and against Neon, that round trip
crosses a region. Pooling keeps the per-request shape the rest of the service already has
(open, use, close) while the expensive part happens once.

PREPARED STATEMENTS ARE OFF, and this is the non-obvious line in the file. Neon's *pooled*
endpoint is PgBouncer in transaction mode, which hands each transaction whichever backend is
free — so a statement psycopg prepared on one backend is missing on the next, and the failure
arrives as `prepared statement "_pg3_0" does not exist` after the fifth execution of a query
(psycopg's prepare threshold), i.e. under load and never in a quick test. `prepare_threshold =
None` is what makes the pooled endpoint safe to use.

AUTOCOMMIT IS OFF, deliberately. Every caller of this pool is doing what the SQLite code did:
several statements that must land together, ending in one commit. The connection context
manager commits on a clean exit and rolls back on an exception, which is the same contract
`_db()` already gives its callers — so the payment-and-its-ledger-grant guarantee survives the
backend swap untouched.

IT IS A PROCESS SINGLETON, built lazily on first use. Uvicorn runs one process per task here, so
one pool per process is one pool per task; building it at import instead would make importing
this module require a reachable database, which the tests and the SQLite path must not need.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

#: How many connections one task keeps open. Small on purpose: the service is IO-bound on the
#: database, tasks are scaled horizontally rather than vertically, and Neon's free tier counts
#: connections. Raise it when a task's own concurrency, not the task count, is the ceiling.
DEFAULT_MIN_SIZE = 1
DEFAULT_MAX_SIZE = 8

#: Seconds to wait for a free connection before failing the request. Short: a request that
#: cannot get a connection should fail visibly rather than hold a worker until the client
#: gives up, which is how a stalled database turns into a stalled service.
DEFAULT_TIMEOUT_S = 10.0


class PostgresUnavailable(RuntimeError):
    """No DATABASE_URL, or psycopg is not installed. The caller decides whether that is fatal
    (the Postgres backend was asked for) or simply means "this deployment is still on SQLite"."""


_pool: Any = None
_lock = threading.Lock()


def configured() -> bool:
    """Whether this deployment has a Postgres to talk to. The ONE question the rest of the
    service asks — never "which backend is this", because a code path that branches on the
    backend is a code path that only works on one of them."""
    return bool((os.environ.get("DATABASE_URL") or "").strip())


def _build() -> Any:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise PostgresUnavailable(
            "DATABASE_URL is not set; this deployment has no Postgres configured"
        )
    try:
        from psycopg.rows import dict_row  # noqa: PLC0415 - deferred; see module docstring
        from psycopg_pool import ConnectionPool  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover - requirements ship psycopg
        raise PostgresUnavailable(
            "psycopg is not installed; cannot open a Postgres connection"
        ) from e
    return ConnectionPool(
        conninfo=url,
        min_size=DEFAULT_MIN_SIZE,
        max_size=DEFAULT_MAX_SIZE,
        timeout=DEFAULT_TIMEOUT_S,
        # NEON CLOSES IDLE CONNECTIONS (compute autosuspend, proxy idle cutoff — a few minutes),
        # and it closes them SILENTLY: the pool still holds the socket, and the next request
        # executes on it and dies with "SSL connection has been closed unexpectedly" — a 500 on
        # whatever endpoint drew the short straw, then a 200 on the retry once the pool discards
        # the corpse. That flapping was live on staging (logins failing between working ones).
        # Two lines close it:
        #   max_idle  — retire a connection idled past 2 minutes, well inside Neon's cutoff, so
        #               the pool rarely holds a dead one at all;
        #   check     — validate at checkout (one cheap round trip), so a connection that died
        #               anyway is discarded INSIDE the pool instead of 500ing a user request.
        max_idle=120.0,
        check=ConnectionPool.check_connection,
        kwargs={
            # See the module docstring: mandatory on a PgBouncer-fronted endpoint.
            "prepare_threshold": None,
            "autocommit": False,
            # ROWS ARE MAPPINGS, because that is what the existing code reads. Every store was
            # written against `sqlite3.Row` and indexes columns BY NAME (`row["account_id"]`);
            # psycopg's default hands back tuples, which would turn every one of those into a
            # TypeError at runtime rather than an error at import. Matching the row type here
            # is what keeps the ported stores a change of SQL and not a rewrite of their logic.
            "row_factory": dict_row,
            # TCP keepalives, so a NAT/proxy hop cannot silently kill an in-pool socket without
            # the OS noticing — the check above then sees a dead socket instead of a hung one.
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        },
        # Do not block startup on the database being reachable. A task that cannot reach
        # Postgres should fail the REQUEST that needs it, loudly, rather than fail to boot and
        # take the health check (which touches no database) down with it.
        open=True,
    )


def pool() -> Any:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = _build()
    return _pool


@contextmanager
def connection() -> Iterator[Any]:
    """One connection for the duration of one unit of work, committed on a clean exit and
    rolled back on an exception — the same contract the SQLite `_db()` gives, so callers do
    not change shape when the backend does."""
    with pool().connection() as conn:
        yield conn


def close() -> None:
    """Release the pool (test teardown, or a process shutting down cleanly)."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
