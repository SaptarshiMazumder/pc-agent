import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.config import load_config
from agentd.presentation.gateway import Gateway, RunHandle


def _gw():
    return Gateway(config=load_config(), service=None)


async def _long():
    await asyncio.sleep(30)


async def _drain(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_disconnect_aborts_only_that_clients_runs():
    gw = _gw()
    a1, t1 = asyncio.Event(), asyncio.create_task(_long())
    a2, t2 = asyncio.Event(), asyncio.create_task(_long())
    gw.runs["s1"] = RunHandle("r1", "s1", a1, client_id="C1", task=t1)
    gw.runs["s2"] = RunHandle("r2", "s2", a2, client_id="C2", task=t2)

    await gw._abort_client_runs("C1")  # client C1 disconnects

    assert a1.is_set()  # cooperative abort flag set (the loop/tools check it)
    with pytest.raises(asyncio.CancelledError):
        await t1
    assert t1.cancelled()

    # a different client's run is untouched
    assert not a2.is_set() and not t2.done()
    await _drain(t2)


@pytest.mark.asyncio
async def test_abort_handle_false_when_not_running():
    gw = _gw()

    async def quick():
        return 1

    t = asyncio.create_task(quick())
    await t  # already done
    h = RunHandle("r", "s", asyncio.Event(), client_id="C", task=t)
    assert gw._abort_handle(h) is False


@pytest.mark.asyncio
async def test_chat_abort_uses_the_shared_helper():
    gw = _gw()
    a, t = asyncio.Event(), asyncio.create_task(_long())
    gw.runs["s1"] = RunHandle("r1", "s1", a, client_id="C1", task=t)

    res = await gw._chat_abort({"sessionKey": "s1"})
    assert res["aborted"] is True and res["runId"] == "r1"
    assert a.is_set()
    with pytest.raises(asyncio.CancelledError):
        await t

    assert (await gw._chat_abort({"sessionKey": "missing"}))["aborted"] is False


@pytest.mark.asyncio
async def test_disconnect_over_real_websocket_aborts_run():
    """End-to-end: a real client connects, starts a run, then DISCONNECTS, and the
    gateway cancels that run — no client cooperation needed (works for any front-end)."""
    import json

    from websockets.asyncio.client import connect as ws_connect
    from websockets.asyncio.server import serve as ws_serve

    started = asyncio.Event()
    aborted = asyncio.Event()

    class FakeService:
        async def handle_message(self, session_id, text, on_event, abort):
            started.set()
            try:
                while not abort.is_set():  # cooperative abort path
                    await asyncio.sleep(0.02)
                aborted.set()
            except asyncio.CancelledError:  # hard-cancel path
                aborted.set()
                raise

    cfg = load_config()
    gw = Gateway(config=cfg, service=FakeService())
    server = await ws_serve(gw._handle_conn, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        ws = await ws_connect(f"ws://127.0.0.1:{port}")
        await ws.send(json.dumps({
            "type": "req", "id": "1", "method": "chat.send",
            "params": {"sessionKey": "s", "message": "hi"},
        }))
        await asyncio.wait_for(started.wait(), 3)   # the run is now in flight
        await ws.close()                            # client disconnects
        await asyncio.wait_for(aborted.wait(), 3)   # gateway aborted it
    finally:
        server.close()
        await server.wait_closed()
