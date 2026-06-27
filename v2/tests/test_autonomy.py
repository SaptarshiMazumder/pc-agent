"""Phase 2a — heartbeat autonomy: interval parsing, scheduler timing, run-mode scoping."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.services.agent_service import AgentService
from agentd.domain.agent import AgentSpec, RunMode, apply_mode
from agentd.infrastructure.agents import FileAgentRegistry
from agentd.infrastructure.autonomy.scheduler import (
    HeartbeatScheduler,
    in_active_hours,
    parse_active_hours,
    parse_interval,
)
from agentd.infrastructure.prompt import build_system_prompt
from heartbeat_tool import HeartbeatRespondTool


class _T:
    def __init__(self, name):
        self.name = name


# ---- interval / active-hours parsing ---------------------------------------

def test_parse_interval():
    assert parse_interval("30s") == 30
    assert parse_interval("15m") == 900
    assert parse_interval("2h") == 7200
    assert parse_interval("1d") == 86400
    assert parse_interval("") is None
    assert parse_interval(None) is None
    assert parse_interval("nope") is None


def test_active_hours():
    assert parse_active_hours("08:00-22:00") == (480, 1320)
    assert parse_active_hours("") is None
    assert parse_active_hours("bad") is None
    assert in_active_hours(None, 100) is True              # no window = always on
    assert in_active_hours((480, 1320), 600) is True       # 10:00 in 08-22
    assert in_active_hours((480, 1320), 60) is False        # 01:00 out
    assert in_active_hours((1320, 360), 1380) is True       # overnight 22-06: 23:00 in
    assert in_active_hours((1320, 360), 720) is False       # overnight: 12:00 out


# ---- run-mode tool scoping -------------------------------------------------

def test_apply_mode_hides_heartbeat_respond_outside_tick():
    tools = [_T("read"), _T("heartbeat_respond"), _T("web_search")]
    interactive = [t.name for t in apply_mode(tools, RunMode.INTERACTIVE)]
    assert "heartbeat_respond" not in interactive
    assert "read" in interactive and "web_search" in interactive
    heartbeat = [t.name for t in apply_mode(tools, RunMode.HEARTBEAT)]
    assert "heartbeat_respond" in heartbeat


# ---- heartbeat_respond tool ------------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_respond_records():
    res = await HeartbeatRespondTool().execute(
        "c", {"outcome": "nothing-to-do", "notify": False}, asyncio.Event())
    assert res.is_error is False
    assert res.details["outcome"] == "nothing-to-do" and res.details["notify"] is False


# ---- scheduler timing (no real clock) --------------------------------------

class _Reg:
    def __init__(self, specs):
        self._s = specs

    def list_ids(self):
        return list(self._s)

    def get(self, i):
        return self._s[i]


def _spec(hb):
    return AgentSpec(id="a", name="a", workspace=Path("."), state_dir=Path("."), heartbeat=hb)


def test_scheduler_due_timing():
    reg = _Reg({"x": _spec("15m"), "y": _spec(None)})       # y declares no interval
    sched = HeartbeatScheduler(reg, fire=None, enabled=True)
    sched.load(now=1000.0)
    assert sched.due(1000.0) == []                          # first tick is one interval out
    assert sched.due(1000.0 + 900) == ["x"]                 # 15m later -> x due (y never)
    assert sched.due(1000.0 + 900) == []                    # advanced; not due again now
    assert sched.due(1000.0 + 1800) == ["x"]


@pytest.mark.asyncio
async def test_scheduler_disabled_fires_nothing():
    fired = []

    async def fire(agent_id):
        fired.append(agent_id)

    sched = HeartbeatScheduler(_Reg({"x": _spec("1s")}), fire, enabled=False)
    await sched.run()                                       # returns immediately
    assert fired == []


# ---- mode-aware prompt -----------------------------------------------------

def test_prompt_injects_heartbeat_only_when_passed():
    cfg = SimpleNamespace(agent_name="J", workspace=Path("."), agent_id="main")
    spec = AgentSpec(id="main", name="J", workspace=Path("."), state_dir=Path("."),
                     heartbeat_instructions="# Heartbeat checklist\n1. check inbox")
    with_hb = build_system_prompt(cfg, [], "m", agent=spec, heartbeat=spec.heartbeat_instructions)
    without = build_system_prompt(cfg, [], "m", agent=spec, heartbeat="")
    assert "Heartbeat checklist" in with_hb
    assert "Heartbeat checklist" not in without


# ---- registry loads HEARTBEAT.md -------------------------------------------

def test_registry_loads_heartbeat_md(tmp_path):
    agents = tmp_path / "agents"
    a = agents / "watcher"
    a.mkdir(parents=True)
    (a / "agent.toml").write_text('heartbeat = "10m"\n', encoding="utf-8")
    (a / "HEARTBEAT.md").write_text("Check the disk every tick.", encoding="utf-8")
    cfg = SimpleNamespace(agent_name="J", workspace=tmp_path / "ws",
                          state_dir=tmp_path / "st", agents_dir=agents)
    s = FileAgentRegistry(cfg).get("watcher")
    assert s.heartbeat == "10m"
    assert "Check the disk every tick." in s.heartbeat_instructions


# ---- service: heartbeat mode exposes the tool + passes the mode ------------

@pytest.mark.asyncio
async def test_service_heartbeat_mode_assembly():
    captured = {}

    class FakeEngine:
        async def run(self, *, messages, system_prompt, tools, on_event, abort,
                      session=None, model=None):
            captured["tools"] = [getattr(t, "name", "") for t in tools]

    class FakeReg:
        def resolve(self, sid):
            return AgentSpec(id="main", name="J", workspace=Path("."), state_dir=Path("."))

    class FakeSession:
        def load(self):
            return []

        def append(self, m):
            return "id"

    def bp(tools, agent, mode, query=""):
        captured["mode"] = mode
        return "SYS"

    svc = AgentService(
        engine=FakeEngine(),
        tools=[_T("read"), _T("heartbeat_respond")],
        registry=FakeReg(),
        make_session=lambda sid, agent: FakeSession(),
        build_prompt=bp,
    )

    async def sink(_e):
        pass

    await svc.handle_message("agent:main:heartbeat", "tick", sink, asyncio.Event(),
                             mode=RunMode.HEARTBEAT)
    assert captured["mode"] == RunMode.HEARTBEAT
    assert "heartbeat_respond" in captured["tools"]         # exposed on a tick

    # interactive run of the same agent hides it
    await svc.handle_message("agent:main:x", "hi", sink, asyncio.Event())
    assert "heartbeat_respond" not in captured["tools"]
