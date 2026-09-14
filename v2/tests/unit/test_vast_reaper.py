import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from vast.application.instance_settings import InstanceSettings
from vast.application.services.instance_reaper import InstanceReaper
from vast.application.services.instance_service import InstanceService
from vast.domain.errors import MarketplaceError
from vast.domain.instance import MachineInstance, label_for
from vast.infrastructure.sql_instance_store import SqlInstanceStore
from vast.infrastructure.sqlite_schema import _STEPS, create_schema


@pytest.fixture
def rig(tmp_path):
    path = tmp_path / "vast.sqlite"

    @contextmanager
    def db():
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    store = SqlInstanceStore()
    with db() as c:
        create_schema(c)
        row, _ = store.claim(c, "alice", 0)
        store.mark_running(c, row.id, instance_id=123, machine_id=1, hourly_usd=.5, now=0)
        store.mark_ready(c, row.id, url="http://gpu", now=0)
    clock = [1000.0]
    market = Mock()
    market.list_instances.return_value = [MachineInstance(
        instance_id=123, label=label_for(row.id), status="running", machine_id=1,
        hourly_usd=.5, url="http://gpu",
    )]
    market.destroy.side_effect = lambda _: setattr(market.list_instances, "return_value", [])
    probe = Mock()
    probe.busy.return_value = False
    args = dict(db=db, store=store, marketplace=lambda: market,
                settings=InstanceSettings(), now=lambda: clock[0], probe=probe)
    return SimpleNamespace(db=db, store=store, clock=clock, market=market, probe=probe,
                           row_id=row.id, reaper=InstanceReaper(**args), service=InstanceService(**args))


def current(rig):
    with rig.db() as c:
        return rig.store.by_id(c, rig.row_id)


def test_confirmed_idle_is_destroyed_and_slot_released(rig):
    assert rig.reaper.sweep()["idle"] == 1
    rig.market.destroy.assert_called_once_with(123)
    assert not current(rig).live
    assert not current(rig).reap_token


@pytest.mark.parametrize("abandoned", [False, True])
@pytest.mark.parametrize("busy", [True, None])
def test_busy_or_unknown_is_kept_even_after_browser_closes(rig, abandoned, busy):
    if abandoned:
        rig.service.idle("alice")
    rig.probe.busy.return_value = busy
    result = rig.reaper.sweep()
    assert result["busy" if busy else "unknown"] == 1
    assert bool(result["errors"]) == (busy is None)
    rig.market.destroy.assert_not_called()
    assert current(rig).live


def test_new_keepalive_during_probe_wins_over_stale_cleanup(rig):
    def reconnect(*args):
        assert rig.service.heartbeat("alice", 180)[0]
        return False
    rig.probe.busy.side_effect = reconnect
    assert rig.reaper.sweep()["raced"] == 1
    rig.market.destroy.assert_not_called()
    assert current(rig).lease_until == 1180


def test_fresh_lease_skips_probe_and_destroy(rig):
    rig.service.heartbeat("alice", 180)
    rig.reaper.sweep()
    rig.probe.busy.assert_not_called()
    rig.market.destroy.assert_not_called()


def test_concurrent_keepalive_and_cleanup_cannot_both_win(rig):
    snapshot = current(rig)
    start = Barrier(2)

    def cleanup():
        start.wait(timeout=5)
        with rig.db() as c:
            return rig.store.claim_reap(c, snapshot, now=1000, claim_seconds=300)

    def keepalive():
        start.wait(timeout=5)
        return rig.service.heartbeat("alice", 180)[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(cleanup)
        beat = pool.submit(keepalive)
        assert (claim.result(timeout=10) is None) == beat.result(timeout=10)


def test_claim_rejects_keepalive_and_retains_account_slot(rig):
    row = current(rig)
    with rig.db() as c:
        token = rig.store.claim_reap(c, row, now=1000, claim_seconds=300)
    assert token
    assert not rig.service.heartbeat("alice", 180)[0]
    assert not rig.service.ensure("alice").ready
    with rig.db() as c:
        slot, mine = rig.store.claim(c, "alice", 1001)
        assert not mine and slot.id == row.id
        assert rig.store.claim_reap(c, row, now=1001, claim_seconds=300) is None
        assert not rig.store.finish_reap(c, row.id, "wrong-token", reason="idle", now=1001)
    rig.market.create.assert_not_called()


def test_overlapping_sweep_does_not_destroy_twice_or_hold_db_lock(rig):
    def destroy(_):
        assert rig.reaper.sweep()["idle"] == 0
        assert not rig.service.heartbeat("alice")[0]
        with rig.db() as c:
            rig.store.claim(c, "another-account", 1000)
        rig.market.list_instances.return_value = []
    rig.market.destroy.side_effect = destroy
    assert rig.reaper.sweep()["idle"] == 1
    rig.market.destroy.assert_called_once_with(123)


def test_failed_destroy_retains_claim_and_retries_after_expiry(rig):
    rig.market.destroy.side_effect = MarketplaceError("destroy", 503, "unavailable")
    assert rig.reaper.sweep()["errors"]
    assert current(rig).live and current(rig).reap_token
    rig.market.destroy.reset_mock()
    rig.reaper.sweep()
    rig.market.destroy.assert_not_called()
    rig.clock[0] += 301
    rig.market.destroy.side_effect = None
    assert rig.reaper.sweep()["idle"] == 1
    assert not current(rig).live


def test_interrupted_claim_is_recovered_and_old_owner_cannot_finish(rig):
    with rig.db() as c:
        old = rig.store.claim_reap(c, current(rig), now=1000, claim_seconds=300)
    rig.clock[0] = 1301
    with rig.db() as c:
        new = rig.store.claim_reap(c, current(rig), now=1301, claim_seconds=300)
        assert old != new
        assert not rig.store.finish_reap(c, rig.row_id, old, reason="late", now=1301)
        assert rig.store.finish_reap(c, rig.row_id, new, reason="done", now=1301)


def test_late_readiness_result_cannot_resurrect_dead_instance(rig):
    rig.reaper.sweep()
    with rig.db() as c:
        rig.store.mark_ready(c, rig.row_id, url="http://old", now=1100)
    assert not current(rig).live


def test_old_schema_migrates_without_losing_rental(tmp_path):
    with sqlite3.connect(tmp_path / "upgrade.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        for version in (1, 2, 3):
            connection.executescript(_STEPS[version])
        connection.execute("CREATE TABLE vast_schema_version (id INTEGER PRIMARY KEY, version INTEGER)")
        connection.execute("INSERT INTO vast_schema_version VALUES (1, 3)")
        connection.execute("INSERT INTO vast_instances (id,account_id,state,created_at,last_seen_at) VALUES ('old','alice','running',0,0)")
        assert create_schema(connection) == 4
        assert create_schema(connection) == 4
        row = SqlInstanceStore().live_for(connection, "alice")
        assert row.id == "old" and row.reap_token == "" and row.reap_until == 0
