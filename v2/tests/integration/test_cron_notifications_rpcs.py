"""cron.* + notifications.* RPCs through the real dispatch, backed by a real
SqliteTaskStore: the autonomy-off degradation, job add/update/run/remove with registry
validation, run history, and the notification list/ack surface any client renders."""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.notify import Notification
from agentd.infrastructure.tasks import SqliteTaskStore
from agentd.presentation.gateway import Gateway
from agentd.presentation.protocol import Request


def _gw(tmp_path, task_store=None):
    specs = {"main": SimpleNamespace(id="main")}
    return Gateway(
        config=SimpleNamespace(state_dir=tmp_path, workspace=tmp_path / "ws"),
        service=None,
        registry=SimpleNamespace(list_ids=lambda: list(specs), get=lambda a: specs[a]),
        task_store=task_store,
    )


@pytest.fixture
def store(tmp_path):
    s = SqliteTaskStore(tmp_path / "tasks.sqlite")
    yield s
    s.close()


def _call(gw, method, params=None):
    resp = asyncio.run(gw._dispatch(Request(id="1", method=method, params=params or {})))
    return resp


def _ok(gw, method, params=None):
    resp = _call(gw, method, params)
    assert resp.ok, resp.payload
    return resp.payload


def test_autonomy_off_degrades_not_crashes(tmp_path):
    gw = _gw(tmp_path)  # no task store == autonomy off
    assert _ok(gw, "cron.list")["autonomy"] is False
    assert _ok(gw, "notifications.list")["autonomy"] is False
    assert _ok(gw, "notifications.ack", {"id": "x"})["acked"] == 0
    # mutating calls surface the actionable error through the dispatch envelope
    resp = _call(gw, "cron.add", {"payload": "do it"})
    assert resp.ok is False and "autonomy is off" in resp.payload["error"]


def test_cron_add_validates_and_lists(tmp_path, store):
    gw = _gw(tmp_path, task_store=store)

    tid = _ok(gw, "cron.add", {"payload": "check mail", "every": "60s"})["id"]
    jobs = _ok(gw, "cron.list")["jobs"]
    assert [j["id"] for j in jobs] == [tid]
    assert jobs[0]["agentId"] == "main" and jobs[0]["enabled"] is True
    assert jobs[0]["schedule"].startswith("every ")

    # empty payload and an unknown agent are rejected before anything is stored
    assert _call(gw, "cron.add", {}).ok is False
    assert _call(gw, "cron.add", {"payload": "x", "agentId": "ghost", "every": "60s"}).ok is False
    assert len(_ok(gw, "cron.list")["jobs"]) == 1


def test_cron_update_run_remove_lifecycle(tmp_path, store):
    gw = _gw(tmp_path, task_store=store)
    tid = _ok(gw, "cron.add", {"payload": "task", "every": "1h"})["id"]

    # disable, change payload — both land in the store
    _ok(gw, "cron.update", {"id": tid, "enabled": False, "payload": "task v2"})
    t = store.get(tid)
    assert t.enabled == 0 and t.payload == "task v2"

    # nothing to update / unknown id -> error envelope
    assert _call(gw, "cron.update", {"id": tid}).ok is False
    assert _call(gw, "cron.update", {"id": "nope", "enabled": True}).ok is False

    # run-now re-enables and pulls next_due to the present (fires on the next poll)
    _ok(gw, "cron.run", {"id": tid})
    t = store.get(tid)
    assert t.enabled == 1 and t.next_due <= time.time() + 1

    assert _ok(gw, "cron.remove", {"id": tid})["removed"] is True
    assert _ok(gw, "cron.remove", {"id": tid})["removed"] is False  # already gone


def test_cron_runs_history(tmp_path, store):
    gw = _gw(tmp_path, task_store=store)
    tid = _ok(gw, "cron.add", {"payload": "job", "every": "60s"})["id"]
    rid = store.record_run(tid, "main")
    store.finish_run(rid, "ok", outcome="done", detail="all good")

    runs = _ok(gw, "cron.runs", {"id": tid})["runs"]
    assert len(runs) == 1
    r = runs[0]
    assert r["taskId"] == tid and r["status"] == "ok" and r["outcome"] == "done"
    assert r["finishedAt"] is not None and r["durationSec"] is not None
    # the compact cron.list view carries the same run, capped at 10
    assert _ok(gw, "cron.list")["runs"][0]["taskId"] == tid


def test_notifications_list_and_ack(tmp_path, store):
    gw = _gw(tmp_path, task_store=store)
    n1 = store.save(Notification(id="", agent_id="main", kind="cron", text="job done"))
    store.save(Notification(id="", agent_id="support", kind="alert", text="heads up"))

    all_rows = _ok(gw, "notifications.list")["notifications"]
    assert {n["text"] for n in all_rows} == {"job done", "heads up"}
    only_main = _ok(gw, "notifications.list", {"agentId": "main"})["notifications"]
    assert [n["id"] for n in only_main] == [n1]

    # ack one -> it drops from the unread view
    assert _ok(gw, "notifications.ack", {"id": n1})["acked"] == 1
    unread = _ok(gw, "notifications.list", {"unread": True})["notifications"]
    assert [n["text"] for n in unread] == ["heads up"]
    # ack all -> nothing unread left; a nonexistent id acks nothing
    assert _ok(gw, "notifications.ack", {"id": "*"})["acked"] == 1
    assert _ok(gw, "notifications.list", {"unread": True})["notifications"] == []
    assert _ok(gw, "notifications.ack", {"id": "no-such-id"})["acked"] == 0
