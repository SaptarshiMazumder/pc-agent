"""Layer B — project-as-entity: lead/members, project inheritance onto delegated child runs,
@mention delegation directive, and the effective-workspace binding (plan §11)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.agent import AgentSpec
from agent_runtime.infrastructure.memory import projects_store
from agent_runtime.infrastructure.memory.local_store import read_session_meta, write_session_meta

# ---------------------------------------------------------------- projects_store


def test_lead_and_members(tmp_path):
    p = projects_store.create_project(tmp_path, "Q3 Report")
    assert p["defaultAgentId"] == "" and p["members"] == []

    assert projects_store.set_lead(tmp_path, p["id"], "support") is True
    assert projects_store.get_project(tmp_path, p["id"])["defaultAgentId"] == "support"
    assert projects_store.set_lead(tmp_path, "proj-nope", "x") is False

    assert projects_store.set_members(tmp_path, p["id"], ["a", "b", "a", " "]) is True
    assert projects_store.get_project(tmp_path, p["id"])["members"] == ["a", "b"]  # deduped


def test_project_workspace_dir(tmp_path):
    d = projects_store.project_workspace_dir(tmp_path, "proj-x1")
    assert d == Path(tmp_path) / "projects" / "proj-x1" / "workspace"
    assert d.is_dir()  # created on demand


# ---------------------------------------------------------------- gateway RPCs


def _gateway(tmp_path, agents=("main",)):
    from agent_runtime.presentation.gateway import Gateway

    class _WS:
        def __init__(self, sink):
            self.sink = sink

        async def send(self, frame):
            self.sink.append(frame)

    events: list[str] = []
    dirs = {a: tmp_path / "agents" / a for a in agents}
    gw = Gateway(
        config=SimpleNamespace(state_dir=tmp_path),
        service=None,
        registry=SimpleNamespace(
            list_ids=lambda: list(dirs),
            get=lambda a: SimpleNamespace(state_dir=dirs[a]),
        ),
    )
    gw.clients = {_WS(events)}
    return gw, events


def test_set_lead_and_members_rpcs(tmp_path):
    gw, events = _gateway(tmp_path, agents=("main", "support"))
    pid = asyncio.run(gw._projects_create({"name": "R"}))["project"]["id"]

    assert asyncio.run(gw._projects_set_lead({"id": pid, "agentId": "support"}))["ok"]
    assert projects_store.get_project(tmp_path, pid)["defaultAgentId"] == "support"
    # unknown agent refused; empty clears
    assert not asyncio.run(gw._projects_set_lead({"id": pid, "agentId": "ghost"}))["ok"]
    assert asyncio.run(gw._projects_set_lead({"id": pid, "agentId": ""}))["ok"]
    assert projects_store.get_project(tmp_path, pid)["defaultAgentId"] == ""

    out = asyncio.run(gw._projects_member({"id": pid, "agentId": "support"}, add=True))
    assert out["ok"] and out["members"] == ["support"]
    out = asyncio.run(gw._projects_member({"id": pid, "agentId": "support"}, add=False))
    assert out["ok"] and out["members"] == []
    assert any("projects.changed" in f for f in events)


def test_inherit_project_onto_child(tmp_path):
    """A child run delegated FROM a project chat inherits projectId (+internal) so its
    workspace binds to the project and it stays hidden from human lists."""
    gw, _ = _gateway(tmp_path, agents=("main", "support"))
    parent_dir = tmp_path / "agents" / "main"
    write_session_meta(parent_dir, "desk-1", projectId="proj-abc")

    gw._inherit_project("main", "desk-1", "support", "agent:support:sub:1:xyz")
    child = read_session_meta(tmp_path / "agents" / "support", "agent:support:sub:1:xyz")
    assert child.get("projectId") == "proj-abc" and child.get("internal") is True

    # a standalone parent inherits nothing
    gw._inherit_project("main", "desk-2", "support", "agent:support:sub:1:zzz")
    assert read_session_meta(tmp_path / "agents" / "support", "agent:support:sub:1:zzz") == {}


# ---------------------------------------------------------------- agent service


def _service(registry, resolve_workspace=None, engine=None):
    from agent_runtime.application.services.agent_service import AgentService

    class _Session:
        def load(self):
            return []

        def append(self, m):
            pass

    return AgentService(
        engine=engine or SimpleNamespace(),
        tools=[],
        registry=registry,
        make_session=lambda sid, agent: _Session(),
        build_prompt=lambda tools, agent, mode, query="": "PROMPT",
        resolve_workspace=resolve_workspace,
    )


def test_mention_directive(tmp_path):
    registry = SimpleNamespace(
        list_ids=lambda: ["main", "figure-creator"],
        get=lambda a: {
            "main": SimpleNamespace(name="JARVIS"),
            "figure-creator": SimpleNamespace(name="Figure Creator"),
        }[a],
    )
    svc = _service(registry)
    me = SimpleNamespace(id="main")
    msg_tool = SimpleNamespace(name="message_agent")

    # mention by display name -> directive names the id; tool required; self-mention ignored
    d = svc._mention_directive("hey @Figure Creator draw a cell", me, [msg_tool])
    assert "figure-creator" in d and "message_agent" in d
    assert svc._mention_directive("hey @Figure Creator", me, []) == ""  # tool absent
    assert svc._mention_directive("hey @JARVIS do it", me, [msg_tool]) == ""  # self
    assert svc._mention_directive("no mentions here", me, [msg_tool]) == ""
    d2 = svc._mention_directive("ask @figure-creator please", me, [msg_tool])  # by id
    assert "figure-creator" in d2


def test_workspace_binding(tmp_path):
    """handle_message binds RunContext.workspace via resolve_workspace (project chat), and
    falls back to the agent's own workspace when the resolver is absent."""
    from agent_runtime.application.run_context import current_run_context

    agent = AgentSpec(
        id="main",
        name="A",
        workspace=tmp_path / "agents" / "main" / "workspace",
        state_dir=tmp_path / "agents" / "main",
    )
    registry = SimpleNamespace(
        get=lambda a: agent, resolve=lambda k: agent, list_ids=lambda: ["main"]
    )

    seen = {}

    class Engine:
        async def run(self, **kw):
            seen["ws"] = current_run_context().workspace

    async def on_event(e):
        pass

    proj_ws = str(tmp_path / "projects" / "proj-1" / "workspace")
    svc = _service(registry, resolve_workspace=lambda a, sid: proj_ws, engine=Engine())
    asyncio.run(svc.handle_message("desk-1", "hi", on_event, asyncio.Event()))
    assert seen["ws"] == proj_ws  # project chat -> project workspace

    svc2 = _service(registry, resolve_workspace=None, engine=Engine())
    asyncio.run(svc2.handle_message("desk-1", "hi", on_event, asyncio.Event()))
    assert seen["ws"] == str(agent.workspace)  # no resolver -> unchanged behavior
