"""sessions.* RPCs through the real dispatch: list (per-agent / all / project filter),
history, rename, move, duplicate, delete — real SessionStore files on disk, real broadcast
frames to a captured client. The partition rules under test: every row carries its agentId,
internal `agent_…` threads never appear, and an unknown agent id NEVER leaks main's chats."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.messages import AssistantMessage, TextContent, UserMessage
from agent_runtime.infrastructure.memory.local_store import SessionStore, read_session_meta
from agent_runtime.presentation.gateway import Gateway, RunHandle
from agent_runtime.presentation.protocol import Request


class _CapturingWs:
    def __init__(self):
        self.frames: list[str] = []

    async def send(self, frame: str) -> None:
        self.frames.append(frame)

    def events(self, name: str) -> list[dict]:
        out = []
        for f in self.frames:
            obj = json.loads(f)
            if obj.get("event") == name:
                out.append(obj.get("payload") or {})
        return out


def _spec(tmp_path, agent_id):
    d = tmp_path / "agents" / agent_id
    (d / "workspace").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(id=agent_id, workspace=d / "workspace", state_dir=d)


def _gw(tmp_path):
    """Gateway with two real agent partitions (main, support) + a capturing client."""
    specs = {aid: _spec(tmp_path, aid) for aid in ("main", "support")}

    def _get(aid):
        if aid in specs:
            return specs[aid]
        raise KeyError(aid)

    gw = Gateway(
        config=SimpleNamespace(state_dir=tmp_path, workspace=tmp_path / "ws"),
        service=None,
        registry=SimpleNamespace(list_ids=lambda: list(specs), get=_get),
    )
    ws = _CapturingWs()
    gw.clients.add(ws)
    return gw, specs, ws


def _seed(state_dir, key, texts=("hi",)):
    s = SessionStore(state_dir, key)
    s.load()
    for t in texts:
        s.append(UserMessage(content=t))
        s.append(AssistantMessage(content=[TextContent(text=f"re: {t}")], stop_reason="stop"))


def _call(gw, method, params):
    resp = asyncio.run(gw._dispatch(Request(id="1", method=method, params=params)))
    assert resp.ok, resp.payload
    return resp.payload


def test_list_is_agent_scoped_and_hides_internal_threads(tmp_path):
    gw, specs, _ = _gw(tmp_path)
    _seed(specs["main"].state_dir, "chat-1")
    _seed(specs["support"].state_dir, "chat-2")
    _seed(specs["main"].state_dir, "agent:helper:cron:t1")  # internal — never a human chat

    out = _call(gw, "sessions.list", {})
    ids = [s["sessionId"] for s in out["sessions"]]
    assert out["agentId"] == "main"
    assert "chat-1" in ids and "chat-2" not in ids  # the OTHER agent's thread stays out
    assert not any(i.startswith("agent_") for i in ids)  # internal thread hidden
    assert all(s["agentId"] == "main" for s in out["sessions"])


def test_list_all_merges_every_agent(tmp_path):
    gw, specs, _ = _gw(tmp_path)
    _seed(specs["main"].state_dir, "m1")
    _seed(specs["support"].state_dir, "s1")

    rows = _call(gw, "sessions.list", {"all": True})["sessions"]
    by_id = {r["sessionId"]: r["agentId"] for r in rows}
    assert by_id["m1"] == "main" and by_id["s1"] == "support"


def test_unknown_agent_never_leaks_mains_history(tmp_path):
    # the regression this guards: a stale client pointing at a deleted agent used to
    # fall back to main and show main's whole history under the wrong agent.
    gw, specs, _ = _gw(tmp_path)
    _seed(specs["main"].state_dir, "secret-chat")

    assert _call(gw, "sessions.list", {"agentId": "deleted-agent"})["sessions"] == []
    hist = _call(gw, "sessions.history", {"agentId": "deleted-agent", "sessionKey": "secret-chat"})
    assert hist["messages"] == []


def test_history_returns_the_persisted_transcript(tmp_path):
    gw, specs, _ = _gw(tmp_path)
    _seed(specs["main"].state_dir, "conv", texts=("hello",))

    out = _call(gw, "sessions.history", {"sessionKey": "conv"})
    roles = [m.get("role") for m in out["messages"]]
    assert roles == ["user", "assistant"]
    assert out["sessionKey"] == "conv" and out["agentId"] == "main"


def test_rename_writes_manual_title_and_broadcasts(tmp_path):
    gw, specs, ws = _gw(tmp_path)
    _seed(specs["main"].state_dir, "conv")

    out = _call(gw, "sessions.rename", {"sessionKey": "conv", "title": "My chat"})
    assert out["ok"] is True and out["title"] == "My chat"
    meta = read_session_meta(specs["main"].state_dir, "conv")
    assert meta["title"] == "My chat" and meta["manual"] is True
    changed = ws.events("sessions.changed")
    assert changed and changed[-1]["sessionKey"] == "conv" and changed[-1]["agentId"] == "main"
    # missing key -> clean refusal, not an exception
    assert _call(gw, "sessions.rename", {"title": "x"})["ok"] is False


def test_move_tags_the_session_with_a_project(tmp_path):
    gw, specs, _ = _gw(tmp_path)
    _seed(specs["main"].state_dir, "conv")

    out = _call(gw, "sessions.move", {"sessionKey": "conv", "projectId": "proj-1"})
    assert out["ok"] is True
    assert read_session_meta(specs["main"].state_dir, "conv")["projectId"] == "proj-1"
    # the project filter on sessions.list now finds it (cross-agent view)
    rows = _call(gw, "sessions.list", {"projectId": "proj-1"})["sessions"]
    assert [r["sessionId"] for r in rows] == ["conv"]
    # back to standalone
    _call(gw, "sessions.move", {"sessionKey": "conv", "projectId": ""})
    assert _call(gw, "sessions.list", {"projectId": "proj-1"})["sessions"] == []


def test_duplicate_copies_transcript_into_a_new_session(tmp_path):
    gw, specs, ws = _gw(tmp_path)
    _seed(specs["main"].state_dir, "orig", texts=("keep me",))

    out = _call(gw, "sessions.duplicate", {"sessionKey": "orig"})
    assert out["ok"] is True and out["sessionKey"] != "orig"
    copy = _call(gw, "sessions.history", {"sessionKey": out["sessionKey"]})["messages"]
    orig = _call(gw, "sessions.history", {"sessionKey": "orig"})["messages"]
    assert [m.get("role") for m in copy] == [m.get("role") for m in orig] and len(copy) == 2
    assert any(p.get("created") for p in ws.events("sessions.changed"))
    # a key that doesn't exist -> clean refusal
    assert _call(gw, "sessions.duplicate", {"sessionKey": "nope"})["ok"] is False


def test_delete_removes_but_refuses_while_running(tmp_path):
    gw, specs, ws = _gw(tmp_path)
    _seed(specs["main"].state_dir, "conv")

    async def scenario():
        # an in-flight run on the session -> delete must refuse
        task = asyncio.create_task(asyncio.sleep(30))
        gw.runs["conv"] = RunHandle("r1", "conv", asyncio.Event(), client_id="C", task=task)
        busy = await gw._dispatch(
            Request(id="1", method="sessions.delete", params={"sessionKey": "conv"})
        )
        assert busy.payload["ok"] is False and "active run" in busy.payload["error"]
        task.cancel()
        # once the run is gone, delete succeeds and the handle is forgotten
        gw.runs.pop("conv", None)
        done = await gw._dispatch(
            Request(id="2", method="sessions.delete", params={"sessionKey": "conv"})
        )
        assert done.payload["ok"] is True and done.payload["deleted"] is True

    asyncio.run(scenario())
    assert _call(gw, "sessions.list", {})["sessions"] == []
    assert any(p.get("deleted") for p in ws.events("sessions.changed"))
