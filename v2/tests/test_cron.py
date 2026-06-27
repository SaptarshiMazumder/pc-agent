"""Phase 2b — cron: durable task store, the cron tool, and scheduler firing."""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.run_context import RunContext, set_run_context
from agentd.domain.autonomy import ScheduledTask
from agentd.infrastructure.autonomy.scheduler import HeartbeatScheduler
from agentd.infrastructure.tasks import SqliteTaskStore
from cron_tool import CronTool
from agentd.main.container import build_task_store


def _task(**over):
    base = dict(id="t1", agent_id="main", session_key="agent:main:cron", kind="every",
                payload="do x", next_due=1000.0, every_seconds=60.0, enabled=True, created_at=0.0)
    base.update(over)
    return ScheduledTask(**base)


# ---- schedule engine: next-fire --------------------------------------------

def test_next_due_after_interval_and_oneshot():
    from agentd.infrastructure.autonomy.schedule import next_due_after
    assert next_due_after(_task(kind="every", every_seconds=60.0), 1000.0) == 1060.0
    assert next_due_after(_task(kind="at", every_seconds=None), 1000.0) is None


def test_cron_expression_next_fire_with_tz():
    import datetime as dt
    from zoneinfo import ZoneInfo

    from agentd.infrastructure.autonomy.schedule import cron_next, cron_valid, next_due_after
    assert cron_valid("55 19 * * 6") and not cron_valid("nope")
    wed = dt.datetime(2026, 6, 17, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")).timestamp()  # a Wednesday
    nxt = cron_next("55 19 * * 6", "Asia/Tokyo", wed)
    d = dt.datetime.fromtimestamp(nxt, ZoneInfo("Asia/Tokyo"))
    assert d.weekday() == 5 and (d.hour, d.minute) == (19, 55)        # next Saturday 19:55 JST
    # next_due_after recomputes a cron task's next fire from the expression
    task = _task(kind="cron", cron_expr="55 19 * * 6", tz="Asia/Tokyo",
                 every_seconds=None, next_due=wed)
    assert next_due_after(task, wed) == nxt


# ---- SQLite store (CRUD, due, restart recovery) -----------------------------

def test_store_crud_due_and_restart(tmp_path):
    db = tmp_path / "autonomy.sqlite"
    store = SqliteTaskStore(db)
    store.add(_task(id="a", kind="at", every_seconds=None, next_due=500.0))
    store.add(_task(id="b", next_due=2000.0))
    assert {t.id for t in store.list("main")} == {"a", "b"}
    assert [t.id for t in store.due(1000.0)] == ["a"]        # only a is due
    store.advance("a", 1000.0)                                # one-shot -> disabled
    assert store.due(1000.0) == []
    store.close()

    store2 = SqliteTaskStore(db)                              # restart
    assert {t.id for t in store2.list()} == {"a", "b"}        # survived
    assert [t.id for t in store2.due(3000.0)] == ["b"]        # b now due
    store2.close()


def test_store_remove(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.add(_task(id="x"))
    assert store.remove("x") is True
    assert store.remove("x") is False
    store.close()


# ---- cron tool --------------------------------------------------------------

@pytest.mark.asyncio
async def test_cron_tool_add_every_lists_under_calling_agent(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = CronTool(store)
    set_run_context(RunContext("watcher", "agent:watcher:x", "interactive"))
    res = await tool.execute("c", {"action": "add", "every": "15m", "payload": "check inbox"},
                             asyncio.Event())
    assert res.is_error is False
    t = store.list("watcher")[0]
    assert t.agent_id == "watcher" and t.kind == "every" and t.every_seconds == 900.0
    # per-task session key (independent session per job), agent still recoverable from parts[1]
    assert t.session_key == f"agent:watcher:cron:{t.id}" and t.payload == "check inbox"
    listed = await tool.execute("c", {"action": "list"}, asyncio.Event())
    assert "check inbox" in listed.content[0].text
    store.close()


@pytest.mark.asyncio
async def test_cron_tool_daily_anchors_to_local_time(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = CronTool(store)
    set_run_context(RunContext("main", "s", "interactive"))
    await tool.execute("c", {"action": "add", "daily": "19:30", "payload": "spending report"},
                       asyncio.Event())
    t = store.list("main")[0]
    assert t.kind == "every" and t.every_seconds == 86400.0
    from datetime import datetime
    due = datetime.fromtimestamp(t.next_due)
    assert (due.hour, due.minute) == (19, 30)             # anchored to 19:30 local
    assert t.next_due > time.time()                        # in the future
    store.close()


@pytest.mark.asyncio
async def test_cron_tool_in_oneshot_then_remove(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = CronTool(store)
    set_run_context(RunContext("main", "s", "interactive"))
    await tool.execute("c", {"action": "add", "in": "2h", "payload": "ping"}, asyncio.Event())
    t = store.list("main")[0]
    assert t.kind == "at" and t.every_seconds is None and t.next_due > time.time()
    rm = await tool.execute("c", {"action": "remove", "id": t.id}, asyncio.Event())
    assert rm.is_error is False and store.list("main") == []
    store.close()


@pytest.mark.asyncio
async def test_cron_tool_add_cron_expression(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = CronTool(store)
    set_run_context(RunContext("main", "s", "interactive"))
    r = await tool.execute("c", {"action": "add", "cron": "55 19 * * 6",
                                 "tz": "Asia/Tokyo", "payload": "weekly report"}, asyncio.Event())
    assert r.is_error is False
    t = store.list("main")[0]
    assert t.kind == "cron" and t.cron_expr == "55 19 * * 6" and t.tz == "Asia/Tokyo"
    # survives a "restart" with the cron fields intact
    t2 = SqliteTaskStore(tmp_path / "a.sqlite").list("main")[0]
    assert t2.cron_expr == "55 19 * * 6" and t2.tz == "Asia/Tokyo"
    bad = await tool.execute("c", {"action": "add", "cron": "not a cron", "payload": "x"}, asyncio.Event())
    assert bad.is_error is True                                       # invalid expr rejected
    store.close()


@pytest.mark.asyncio
async def test_cron_tool_get_update_run(tmp_path):
    import time as _t
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = CronTool(store)
    set_run_context(RunContext("main", "s", "interactive"))
    await tool.execute("c", {"action": "add", "every": "30m", "payload": "old"}, asyncio.Event())
    tid = store.list("main")[0].id

    got = await tool.execute("c", {"action": "get", "id": tid}, asyncio.Event())
    assert tid in got.content[0].text

    upd = await tool.execute("c", {"action": "update", "id": tid, "daily": "08:00",
                                   "payload": "new"}, asyncio.Event())
    assert upd.is_error is False
    t = store.get(tid)
    assert t.payload == "new" and t.kind == "every" and t.every_seconds == 86400.0

    run = await tool.execute("c", {"action": "run", "id": tid}, asyncio.Event())
    assert run.is_error is False and store.get(tid).next_due <= _t.time() + 1   # due now

    missing = await tool.execute("c", {"action": "get", "id": "nope"}, asyncio.Event())
    assert missing.is_error is True
    store.close()


@pytest.mark.asyncio
async def test_cron_tool_deliver_message_outbox(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = CronTool(store)
    set_run_context(RunContext("main", "s", "interactive"))
    await tool.execute("c", {"action": "add", "in": "1h", "payload": "Happy birthday!",
                             "deliver": "message"}, asyncio.Event())
    t = store.list("main")[0]
    assert t.delivery == "message" and t.payload == "Happy birthday!" and t.kind == "at"
    store.close()


@pytest.mark.asyncio
async def test_cron_tool_rejects_missing_schedule_or_payload(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = CronTool(store)
    set_run_context(RunContext("main", "s", "interactive"))
    assert (await tool.execute("c", {"action": "add", "payload": "x"}, asyncio.Event())).is_error
    assert (await tool.execute("c", {"action": "add", "every": "5m"}, asyncio.Event())).is_error
    store.close()


# ---- scheduler fires due tasks ---------------------------------------------

class _EmptyReg:
    def list_ids(self):
        return []

    def get(self, i):
        raise KeyError(i)


@pytest.mark.asyncio
async def test_scheduler_fires_due_tasks_and_advances(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.add(_task(id="due1", kind="at", every_seconds=None, next_due=time.time() - 1))
    store.add(_task(id="future", next_due=time.time() + 9999))
    fired = []

    async def fire_task(task):
        fired.append(task.id)
        return True

    sched = HeartbeatScheduler(_EmptyReg(), fire=None, enabled=True,
                               task_store=store, fire_task=fire_task, poll_seconds=0.01)
    runner = asyncio.create_task(sched.run())
    await asyncio.sleep(0.06)
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass
    assert "due1" in fired and "future" not in fired
    assert all(t.id != "due1" for t in store.due(time.time()))   # one-shot advanced -> disabled
    store.close()


@pytest.mark.asyncio
async def test_scheduler_busy_lane_leaves_task_due(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.add(_task(id="once", kind="at", every_seconds=None, next_due=time.time() - 1))
    calls = []

    async def fire_task(task):
        calls.append(task.id)
        return False                      # lane busy -> not fired

    sched = HeartbeatScheduler(_EmptyReg(), fire=None, enabled=True,
                               task_store=store, fire_task=fire_task, poll_seconds=0.01)
    runner = asyncio.create_task(sched.run())
    await asyncio.sleep(0.04)
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass
    assert len(calls) >= 2                 # retried (not advanced/dropped while busy)
    assert [t.id for t in store.due(time.time())] == ["once"]
    store.close()


# ---- run history (audit) ----------------------------------------------------

def test_store_run_history(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    rid = store.record_run("t1", "main")
    assert store.recent_runs()[0].status == "running"
    store.finish_run(rid, "ok")
    r = store.recent_runs(task_id="t1")[0]
    assert r.status == "ok" and r.finished_at is not None
    store.record_run("t2", "support")
    assert len(store.recent_runs(agent_id="support")) == 1     # filtered by agent
    assert len(store.recent_runs()) == 2                        # all
    store.close()


def test_store_run_outcome(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.finish_run(store.record_run("t1", "main"), "blocked",
                     outcome="blocked", detail="needs Drive auth")
    r = store.recent_runs(task_id="t1")[0]
    assert r.status == "blocked" and r.outcome == "blocked" and r.detail == "needs Drive auth"
    store.finish_run(store.record_run("t2", "main"), "ok")      # no outcome declared
    r2 = store.recent_runs(task_id="t2")[0]
    assert r2.status == "ok" and r2.outcome is None and r2.detail == ""
    store.close()


@pytest.mark.asyncio
async def test_cron_tool_status_runs_wake(tmp_path):
    import time as _t
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = CronTool(store)
    # two agents schedule jobs; record a run for alpha
    set_run_context(RunContext("alpha", "s", "interactive"))
    await tool.execute("c", {"action": "add", "every": "30m", "payload": "a"}, asyncio.Event())
    set_run_context(RunContext("beta", "s", "interactive"))
    await tool.execute("c", {"action": "add", "daily": "09:00", "payload": "b"}, asyncio.Event())
    store.finish_run(store.record_run("tX", "alpha"), "ok")

    # status = overall + per-agent dashboard
    st = (await tool.execute("c", {"action": "status"}, asyncio.Event())).content[0].text
    assert "2 active job(s) across 2 agent(s)" in st and "alpha" in st and "beta" in st
    assert "Recent runs" in st

    # runs = the calling agent's history
    set_run_context(RunContext("alpha", "s", "interactive"))
    rn = (await tool.execute("c", {"action": "runs"}, asyncio.Event())).content[0].text
    assert "tX" in rn and "ok" in rn

    # wake = an immediate one-shot for the calling agent (or a named one)
    set_run_context(RunContext("main", "s", "interactive"))
    w = await tool.execute("c", {"action": "wake", "text": "check the build now"}, asyncio.Event())
    assert w.is_error is False
    t = store.list("main")[0]
    assert t.kind == "at" and t.payload == "check the build now"
    assert t.id in [x.id for x in store.due(_t.time() + 1)]      # due immediately
    await tool.execute("c", {"action": "wake", "text": "ping", "agentId": "other"}, asyncio.Event())
    assert store.list("other")[0].payload == "ping"
    store.close()


# ---- gateway cron.list (the client-facing 'list jobs myself' surface) -------

def test_gateway_cron_list(tmp_path):
    from agentd.presentation.gateway import Gateway
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.add(_task(id="j1", agent_id="alpha", kind="cron", cron_expr="55 19 * * 6",
                    tz="Asia/Tokyo", every_seconds=None, next_due=time.time() + 100, payload="weekly"))
    store.finish_run(store.record_run("j1", "alpha"), "ok")
    cfg = SimpleNamespace(agent_name="J", agent_id="main")
    out = Gateway(config=cfg, service=None, task_store=store)._cron_list()
    assert out["autonomy"] is True
    j = out["jobs"][0]
    assert j["id"] == "j1" and j["agentId"] == "alpha" and "cron '55 19 * * 6'" in j["schedule"]
    assert out["runs"][0]["taskId"] == "j1" and out["runs"][0]["status"] == "ok"
    # autonomy off -> no store -> empty + flagged
    assert Gateway(config=cfg, service=None, task_store=None)._cron_list()["autonomy"] is False
    store.close()


def test_gateway_cron_crud(tmp_path):
    import time as _t

    from agentd.presentation.gateway import Gateway

    class _Reg:
        def list_ids(self):
            return ["main", "spending-agent"]

    store = SqliteTaskStore(tmp_path / "a.sqlite")
    cfg = SimpleNamespace(agent_name="J", agent_id="main")
    gw = Gateway(config=cfg, service=None, registry=_Reg(), task_store=store)

    tid = gw._cron_add({"agentId": "spending-agent", "cron": "55 19 * * 6",
                        "tz": "Asia/Tokyo", "payload": "weekly"})["id"]
    t = store.get(tid)
    assert t.agent_id == "spending-agent" and t.kind == "cron" and t.cron_expr == "55 19 * * 6"

    gw._cron_update({"id": tid, "daily": "08:00", "payload": "new"})       # reschedule + payload
    t = store.get(tid)
    assert t.kind == "every" and t.payload == "new"

    gw._cron_update({"id": tid, "enabled": False})                          # disable
    assert store.get(tid).enabled is False

    gw._cron_run({"id": tid})                                               # run now -> due + re-enabled
    assert store.get(tid).next_due <= _t.time() + 1 and store.get(tid).enabled is True

    assert gw._cron_remove({"id": tid})["removed"] is True                  # remove
    assert store.get(tid) is None

    with pytest.raises(ValueError):                                         # unknown agent
        gw._cron_add({"agentId": "nope", "every": "1h", "payload": "x"})
    with pytest.raises(RuntimeError):                                       # autonomy off
        Gateway(config=cfg, service=None, task_store=None)._cron_remove({"id": "x"})
    store.close()


def test_gateway_cron_runs_full_history(tmp_path):
    from agentd.presentation.gateway import Gateway
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.finish_run(store.record_run("t1", "alpha"), "ok")
    store.finish_run(store.record_run("t1", "alpha"), "error")
    store.record_run("t2", "beta")                       # still running (no finish)
    cfg = SimpleNamespace(agent_name="J", agent_id="main")
    gw = Gateway(config=cfg, service=None, task_store=store)

    out = gw._cron_runs({})
    assert out["autonomy"] is True and len(out["runs"]) == 3
    assert {r["status"] for r in out["runs"]} == {"ok", "error", "running"}
    durs = {r["status"]: r["durationSec"] for r in out["runs"]}
    assert durs["running"] is None and durs["ok"] is not None and durs["error"] is not None  # finished have a duration
    assert all("outcome" in r and "detail" in r for r in out["runs"])   # outcome surfaced

    # a blocked outcome shows through cron.runs
    store.finish_run(store.record_run("t3", "alpha"), "blocked",
                     outcome="blocked", detail="needs Drive auth")
    blocked = gw._cron_runs({"id": "t3"})["runs"][0]
    assert blocked["status"] == "blocked" and blocked["detail"] == "needs Drive auth"

    only_t1 = gw._cron_runs({"id": "t1"})["runs"]         # filter by job
    assert len(only_t1) == 2 and all(r["taskId"] == "t1" for r in only_t1)

    assert Gateway(config=cfg, service=None, task_store=None)._cron_runs({})["autonomy"] is False
    store.close()


# ---- back-compat ------------------------------------------------------------

def test_no_ledger_when_autonomy_off():
    assert build_task_store(SimpleNamespace(autonomy_enabled=False)) is None
