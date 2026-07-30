"""Phase 5a — Notify: the agent reaches the user (store + adapters + gateway)."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.domain.notify import Notification
from agent_runtime.infrastructure.notify import (
    ClientPushNotifier,
    CompositeNotifier,
    StoreNotifier,
    build_notifier,
)
from agent_runtime.infrastructure.tasks import SqliteTaskStore


def _notif(**over):
    base = dict(id="", agent_id="main", kind="blocked", text="t", detail="d", created_at=0.0)
    base.update(over)
    return Notification(**base)


async def _async_append(lst, x):
    lst.append(x)


# ---- store ------------------------------------------------------------------


def test_notify_store_roundtrip_filters_ack(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    nid = store.save(_notif(agent_id="spending-agent", text="blocked", detail="needs Drive auth"))
    rows = store.notifications()
    assert len(rows) == 1 and rows[0].id == nid and rows[0].read is False
    assert rows[0].detail == "needs Drive auth"

    store.save(_notif(agent_id="other", kind="failed"))
    assert len(store.notifications(agent_id="spending-agent")) == 1  # agent filter
    assert len(store.notifications(unread_only=True)) == 2  # both unread

    assert store.ack(nid) is True  # ack one
    assert len(store.notifications(unread_only=True)) == 1
    assert store.notifications(agent_id="spending-agent")[0].read is True
    store.close()


# ---- adapters ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_push_notifier_calls_broadcast():
    seen = []
    await ClientPushNotifier(lambda n: _async_append(seen, n)).notify(_notif(text="hi"))
    assert seen and seen[0].text == "hi"


@pytest.mark.asyncio
async def test_store_notifier_persists(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    await StoreNotifier(store).notify(_notif(text="persist me"))
    assert store.notifications()[0].text == "persist me"
    store.close()


@pytest.mark.asyncio
async def test_composite_fans_out_and_isolates_failures(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    pushed = []

    class _Boom:
        async def notify(self, n):
            raise RuntimeError("boom")

    comp = CompositeNotifier(
        [_Boom(), ClientPushNotifier(lambda n: _async_append(pushed, n)), StoreNotifier(store)]
    )
    await comp.notify(_notif(text="x"))  # _Boom raises but is isolated
    assert pushed and store.notifications()[0].text == "x"
    store.close()


def test_build_notifier_composition(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    assert len(build_notifier(store, lambda n: None)._notifiers) == 2  # push + store
    assert len(build_notifier(None, lambda n: None)._notifiers) == 1  # push only
    store.close()


# ---- gateway ----------------------------------------------------------------


def test_gateway_notifications_list_and_ack(tmp_path):
    from agent_runtime.presentation.gateway import Gateway

    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.save(_notif(agent_id="a", text="one"))
    store.save(_notif(agent_id="a", text="two", kind="failed"))
    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path), service=None, task_store=store)

    out = gw._notifications_list({})
    assert out["autonomy"] is True and len(out["notifications"]) == 2
    nid = out["notifications"][0]["id"]
    assert gw._notifications_ack({"id": nid})["acked"] == 1
    assert gw._notifications_ack({"id": "*"})["acked"] == 1  # remaining one
    assert gw._notifications_list({"unread": True})["notifications"] == []

    off = Gateway(config=SimpleNamespace(state_dir=tmp_path), service=None, task_store=None)
    assert off._notifications_list({})["autonomy"] is False
    store.close()


@pytest.mark.asyncio
async def test_gateway_notify_run_builds_notification(tmp_path):
    from agent_runtime.presentation.gateway import Gateway, RunHandle

    seen = []

    class _Fake:
        async def notify(self, n):
            seen.append(n)

    gw = Gateway(
        config=SimpleNamespace(state_dir=tmp_path),
        service=None,
        task_store=SqliteTaskStore(tmp_path / "a.sqlite"),
        notifier=_Fake(),
    )
    handle = RunHandle(run_id="r", session_key="agent:spending-agent:cron", abort=asyncio.Event())
    await gw._notify_run(handle, "blocked", "needs Drive auth")
    assert seen[0].agent_id == "spending-agent" and seen[0].kind == "blocked"
    assert "needs Drive auth" in seen[0].detail
    gw.task_store.close()


@pytest.mark.asyncio
async def test_gateway_push_notification_frame(tmp_path):
    from agent_runtime.presentation.gateway import Gateway

    sent = []

    class _WS:
        async def send(self, frame):
            sent.append(frame)

    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path), service=None, task_store=None)
    gw.clients.add(_WS())
    await gw._push_notification(_notif(text="ping", detail="d", agent_id="a", kind="blocked"))
    frame = json.loads(sent[0])
    assert frame["type"] == "event" and frame["event"] == "notification"
    assert frame["payload"]["text"] == "ping" and frame["payload"]["agentId"] == "a"
