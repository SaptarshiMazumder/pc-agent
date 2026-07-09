"""Projects: store CRUD + gateway RPCs + the chat.send -> session-meta link."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentd.infrastructure.memory import projects_store
from agentd.infrastructure.memory.local_store import (
    SessionStore,
    read_session_meta,
    sessions_in_project,
    write_session_meta,
)


def test_projects_store_crud(tmp_path):
    assert projects_store.list_projects(tmp_path) == []
    p = projects_store.create_project(tmp_path, "  Thesis   Figures  ")
    assert p["name"] == "Thesis Figures" and p["id"].startswith("proj-")
    assert p["createdAt"] > 0

    assert projects_store.rename_project(tmp_path, p["id"], "Thesis") is True
    assert projects_store.get_project(tmp_path, p["id"])["name"] == "Thesis"
    assert projects_store.rename_project(tmp_path, "proj-nope", "x") is False
    assert projects_store.rename_project(tmp_path, p["id"], "   ") is False

    assert projects_store.delete_project(tmp_path, p["id"]) is True
    assert projects_store.delete_project(tmp_path, p["id"]) is False
    assert projects_store.list_projects(tmp_path) == []

    with pytest.raises(ValueError):
        projects_store.create_project(tmp_path, "   ")


def test_sessions_in_project(tmp_path):
    SessionStore(tmp_path, "s1").load()
    SessionStore(tmp_path, "s2").load()
    write_session_meta(tmp_path, "s1", projectId="proj-a")
    assert sessions_in_project(tmp_path, "proj-a") == ["s1"]
    assert sessions_in_project(tmp_path, "proj-zzz") == []
    assert sessions_in_project(tmp_path, "") == []


def _gateway(tmp_path):
    from agentd.presentation.gateway import Gateway

    class _WS:
        def __init__(self, sink):
            self.sink = sink

        async def send(self, frame):
            self.sink.append(frame)

    events: list[str] = []
    gw = Gateway(
        config=SimpleNamespace(state_dir=tmp_path),
        service=None,
        registry=SimpleNamespace(
            list_ids=lambda: ["main"],
            get=lambda a: SimpleNamespace(state_dir=tmp_path),
        ),
    )
    gw.clients = {_WS(events)}
    return gw, events


def test_gateway_projects_rpcs(tmp_path):
    gw, events = _gateway(tmp_path)

    out = asyncio.run(gw._projects_create({"name": "Research"}))
    pid = out["project"]["id"]
    assert out["ok"] and gw._projects_list()["projects"][0]["name"] == "Research"
    assert any("projects.changed" in f for f in events)

    assert asyncio.run(gw._projects_rename({"id": pid, "name": "Research 2"}))["ok"]
    assert gw._projects_list()["projects"][0]["name"] == "Research 2"

    # a session tagged into the project; deleting WITHOUT deleteSessions untags it
    SessionStore(tmp_path, "chat1").load()
    write_session_meta(tmp_path, "chat1", projectId=pid)
    out = asyncio.run(gw._projects_delete({"id": pid}))
    assert out["ok"] and out["sessionsDeleted"] == 0
    assert read_session_meta(tmp_path, "chat1").get("projectId") == ""
    assert (tmp_path / "sessions" / "chat1.jsonl").exists()  # chat survives

    # deleteSessions=True removes the chats too
    out2 = asyncio.run(gw._projects_create({"name": "Temp"}))
    SessionStore(tmp_path, "chat2").load()
    write_session_meta(tmp_path, "chat2", projectId=out2["project"]["id"])
    out3 = asyncio.run(gw._projects_delete({"id": out2["project"]["id"], "deleteSessions": True}))
    assert out3["ok"] and out3["sessionsDeleted"] == 1
    assert not (tmp_path / "sessions" / "chat2.jsonl").exists()


def test_chat_send_tags_session_with_project(tmp_path):
    """chat.send {projectId} writes the link onto the session's meta sidecar —
    the ONE place project membership lives, shared by every client."""
    gw, _events = _gateway(tmp_path)

    async def fake_run(handle, message, mode="interactive", agent_id=None, attachments=None):
        return None

    gw._run = fake_run  # the run itself is not under test

    async def go():
        out = await gw._chat_send(
            {"sessionKey": "desk-x1", "message": "hi", "agentId": "main", "projectId": "proj-abc"},
            None,
        )
        await gw.runs["desk-x1"].task
        return out

    assert asyncio.run(go())["runId"]
    assert read_session_meta(tmp_path, "desk-x1").get("projectId") == "proj-abc"
