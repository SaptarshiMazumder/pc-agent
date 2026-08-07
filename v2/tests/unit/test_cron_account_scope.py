"""Scheduled tasks belong to the account that created them.

The scheduler fires from a heartbeat loop with no connection behind it, so nothing about a due
task's execution context comes from the caller — it has to come from the row. Before this, a
hosted user's cron ran with no account at all: reading and writing the SHARED state instead of
theirs, unattended, on a timer. Not a crash; just someone else's files.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.autonomy import ScheduledTask
from agent_runtime.infrastructure import accounts
from agent_runtime.infrastructure.tasks.sqlite_store import SqliteTaskStore


@pytest.fixture
def store(tmp_path):
    s = SqliteTaskStore(tmp_path / "autonomy.sqlite")
    yield s
    s.close()


def _task(task_id: str, agent_id: str = "main", **over) -> ScheduledTask:
    base = {
        "id": task_id,
        "agent_id": agent_id,
        "session_key": f"{agent_id}:1",
        "kind": "every",
        "payload": "do the thing",
        "next_due": 0.0,
        "every_seconds": 60.0,
        "created_at": 1.0,
    }
    base.update(over)
    return ScheduledTask(**base)


class _As:
    """Run a block as an account (what a connection's contextvar does for real callers)."""

    def __init__(self, account_id):
        self._account_id = account_id
        self._token = None

    def __enter__(self):
        self._token = accounts.set_account(
            {"account_id": self._account_id} if self._account_id else None
        )
        return self

    def __exit__(self, *exc):
        accounts.reset_account(self._token)
        return False


def test_add_stamps_the_creating_account(store):
    with _As("acct_a"):
        store.add(_task("t1"))
    assert store.get("t1").account_id == "acct_a"


def test_desktop_tasks_carry_no_account(store):
    store.add(_task("t1"))
    assert store.get("t1").account_id == ""


def test_an_explicit_account_wins_over_the_contextvar(store):
    """Restore/import paths hand the row its owner; they must not be re-stamped as the importer."""
    with _As("acct_importer"):
        store.add(_task("t1", account_id="acct_original"))
    assert store.get("t1").account_id == "acct_original"


def test_list_shows_only_the_callers_tasks(store):
    with _As("acct_a"):
        store.add(_task("t1"))
    with _As("acct_b"):
        store.add(_task("t2"))

    with _As("acct_a"):
        assert [t.id for t in store.list()] == ["t1"]
    with _As("acct_b"):
        assert [t.id for t in store.list()] == ["t2"]


def test_list_filters_by_account_and_agent_together(store):
    with _As("acct_a"):
        store.add(_task("t1", agent_id="main"))
        store.add(_task("t2", agent_id="other"))
    with _As("acct_b"):
        store.add(_task("t3", agent_id="main"))

    with _As("acct_a"):
        assert [t.id for t in store.list("main")] == ["t1"]


def test_no_account_sees_everything(store):
    """Desktop is unscoped — one user, and the scheduler needs the whole list."""
    with _As("acct_a"):
        store.add(_task("t1"))
    store.add(_task("t2"))
    assert {t.id for t in store.list()} == {"t1", "t2"}


def test_due_ignores_the_account(store):
    """`due()` feeds the SCHEDULER, which must see every account's work. Isolation happens when
    the run starts (the fire path sets the account from the row), not by hiding rows here — a
    filtered due() would simply never run a hosted user's task."""
    with _As("acct_a"):
        store.add(_task("t1"))
    with _As("acct_b"):
        store.add(_task("t2"))
    assert {t.id for t in store.due(now=1000.0)} == {"t1", "t2"}


def test_the_owner_survives_a_reload(tmp_path):
    """The column is what the fire path reads; an in-memory-only stamp would be worthless."""
    path = tmp_path / "autonomy.sqlite"
    first = SqliteTaskStore(path)
    with _As("acct_a"):
        first.add(_task("t1"))
    first.close()

    second = SqliteTaskStore(path)
    try:
        assert second.get("t1").account_id == "acct_a"
    finally:
        second.close()


def test_an_older_db_migrates_without_losing_tasks(tmp_path):
    """The column is added by ALTER on an existing DB — pre-accounts rows must survive as
    unowned (desktop) rather than vanish from list()."""
    import sqlite3

    path = tmp_path / "autonomy.sqlite"
    legacy = SqliteTaskStore(path)
    legacy.add(_task("old"))
    legacy.close()
    # Simulate a DB written before the column existed.
    raw = sqlite3.connect(str(path))
    raw.execute("ALTER TABLE tasks DROP COLUMN account_id")
    raw.commit()
    raw.close()

    migrated = SqliteTaskStore(path)
    try:
        assert [t.id for t in migrated.list()] == ["old"]
        assert migrated.get("old").account_id == ""
    finally:
        migrated.close()
