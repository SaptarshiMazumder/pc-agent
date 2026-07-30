"""projects.* RPCs through the real dispatch: create/list/rename, lead + roster with
registry validation, and delete's session fan-out (untag by default, deleteSessions=true
purges across EVERY agent partition) — real projects_store + real session sidecars."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.messages import UserMessage
from agent_runtime.infrastructure.memory.local_store import (
    SessionStore,
    read_session_meta,
    write_session_meta,
)
from agent_runtime.presentation.gateway import Gateway
from agent_runtime.presentation.protocol import Request


def _spec(tmp_path, agent_id):
    d = tmp_path / "agents" / agent_id
    (d / "workspace").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(id=agent_id, workspace=d / "workspace", state_dir=d)


def _gw(tmp_path):
    specs = {aid: _spec(tmp_path, aid) for aid in ("main", "support")}

    def _get(aid):
        if aid in specs:
            return specs[aid]
        raise KeyError(aid)

    return Gateway(
        config=SimpleNamespace(state_dir=tmp_path, workspace=tmp_path / "ws"),
        service=None,
        registry=SimpleNamespace(list_ids=lambda: list(specs), get=_get),
    ), specs


def _call(gw, method, params=None):
    resp = asyncio.run(gw._dispatch(Request(id="1", method=method, params=params or {})))
    assert resp.ok, resp.payload
    return resp.payload


def test_create_list_rename_roundtrip(tmp_path):
    gw, _ = _gw(tmp_path)
    created = _call(gw, "projects.create", {"name": "Q3 Report"})["project"]
    assert created["name"] == "Q3 Report"

    rows = _call(gw, "projects.list")["projects"]
    assert [p["id"] for p in rows] == [created["id"]]

    assert _call(gw, "projects.rename", {"id": created["id"], "name": "Q4"})["ok"] is True
    assert _call(gw, "projects.list")["projects"][0]["name"] == "Q4"
    # unknown id -> ok False, no exception
    assert _call(gw, "projects.rename", {"id": "proj-nope", "name": "x"})["ok"] is False


def test_lead_and_members_validate_against_the_registry(tmp_path):
    gw, _ = _gw(tmp_path)
    pid = _call(gw, "projects.create", {"name": "P"})["project"]["id"]

    # lead: a real agent is accepted, a ghost is rejected with a clear error
    assert _call(gw, "projects.setLead", {"id": pid, "agentId": "support"})["ok"] is True
    ghost = _call(gw, "projects.setLead", {"id": pid, "agentId": "ghost"})
    assert ghost["ok"] is False and "unknown agent" in ghost["error"]
    lead = next(p for p in _call(gw, "projects.list")["projects"] if p["id"] == pid)
    assert lead["defaultAgentId"] == "support"

    # roster: add validates the agent; remove doesn't need to (it's already listed)
    assert _call(gw, "projects.addMember", {"id": pid, "agentId": "support"})["members"] == [
        "support"
    ]
    bad = _call(gw, "projects.addMember", {"id": pid, "agentId": "ghost"})
    assert bad["ok"] is False and "unknown agent" in bad["error"]
    assert _call(gw, "projects.removeMember", {"id": pid, "agentId": "support"})["members"] == []
    # unknown project -> clean refusal
    assert _call(gw, "projects.addMember", {"id": "nope", "agentId": "support"})["ok"] is False


def _seed_project_session(state_dir, key, project_id):
    s = SessionStore(state_dir, key)
    s.load()
    s.append(UserMessage(content="hi"))
    write_session_meta(state_dir, key, projectId=project_id)


def test_delete_untags_sessions_by_default(tmp_path):
    gw, specs = _gw(tmp_path)
    pid = _call(gw, "projects.create", {"name": "P"})["project"]["id"]
    _seed_project_session(specs["main"].state_dir, "m-chat", pid)
    _seed_project_session(specs["support"].state_dir, "s-chat", pid)

    out = _call(gw, "projects.delete", {"id": pid})
    assert out["ok"] is True and out["sessionsDeleted"] == 0
    # chats survive as standalone — in BOTH partitions
    assert read_session_meta(specs["main"].state_dir, "m-chat")["projectId"] == ""
    assert read_session_meta(specs["support"].state_dir, "s-chat")["projectId"] == ""
    assert _call(gw, "projects.list")["projects"] == []


def test_delete_with_delete_sessions_purges_across_partitions(tmp_path):
    gw, specs = _gw(tmp_path)
    pid = _call(gw, "projects.create", {"name": "P"})["project"]["id"]
    _seed_project_session(specs["main"].state_dir, "m-chat", pid)
    _seed_project_session(specs["support"].state_dir, "s-chat", pid)
    _seed_project_session(specs["main"].state_dir, "other", "different-project")

    out = _call(gw, "projects.delete", {"id": pid, "deleteSessions": True})
    assert out["ok"] is True and out["sessionsDeleted"] == 2
    # the two project chats are gone; the unrelated one is untouched
    ids = [s["sessionId"] for s in _call(gw, "sessions.list", {"all": True})["sessions"]]
    assert ids == ["other"]
    # missing id -> clean refusal
    assert _call(gw, "projects.delete", {})["ok"] is False
