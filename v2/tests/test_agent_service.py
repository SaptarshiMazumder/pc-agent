"""Locks the AgentService orchestration with fakes (no IO, no LLM)."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pathlib import Path

from agentd.application import run_context as rc
from agentd.application.run_context import set_run_outcome
from agentd.application.services.agent_service import AgentService
from agentd.domain.agent import AgentSpec, RunMode
from agentd.domain.messages import UserMessage


async def _sink(_ev):
    pass


class FakeRegistry:
    def __init__(self, spec):
        self._spec = spec

    def resolve(self, session_key):
        return self._spec

    def get(self, agent_id):
        return self._spec

    def list_ids(self):
        return [self._spec.id]


class FakeSession:
    def __init__(self):
        self.store = []

    def load(self):
        return list(self.store)

    def append(self, m):
        self.store.append(m)
        return "id"


class FakeEngine:
    def __init__(self):
        self.calls = []

    async def run(self, *, messages, system_prompt, tools, on_event, abort, session=None,
                  model=None):
        self.calls.append(
            {"messages": list(messages), "system_prompt": system_prompt,
             "tools": tools, "session": session, "model": model}
        )
        return []


@pytest.mark.asyncio
async def test_handle_message_orchestration():
    sess = FakeSession()
    engine = FakeEngine()
    spec = AgentSpec(id="main", name="JARVIS", workspace=Path("."), state_dir=Path("."))
    svc = AgentService(
        engine=engine,
        tools=["read", "exec"],
        registry=FakeRegistry(spec),
        make_session=lambda sid, agent: sess,
        build_prompt=lambda tools, agent, mode, query="": f"SYS({len(tools)})",
    )

    async def sink(_ev):
        pass

    await svc.handle_message("s1", "hello", sink, asyncio.Event())

    # the user message was appended to (persisted in) the session
    assert len(sess.store) == 1
    assert isinstance(sess.store[0], UserMessage) and sess.store[0].content == "hello"

    # the engine was run once, with the user message in context, the built prompt,
    # the tools, and the same session handed through for persistence
    assert len(engine.calls) == 1
    call = engine.calls[0]
    assert call["messages"][-1].content == "hello"
    assert call["system_prompt"] == "SYS(2)"
    assert call["tools"] == ["read", "exec"]
    assert call["session"] is sess
    assert call["model"] is None  # main has no override -> engine uses its default


@pytest.mark.asyncio
async def test_explicit_agent_id_overrides_session_key_resolution():
    # a client naming an agent (agent_id) wins over what the session key would resolve to
    main = AgentSpec(id="main", name="Main", workspace=Path("."), state_dir=Path("."))
    support = AgentSpec(id="support", name="Support", workspace=Path("."), state_dir=Path("."))

    class TwoAgentRegistry:
        def resolve(self, session_key):
            return main                       # the session key would say 'main'
        def get(self, agent_id):
            return {"main": main, "support": support}[agent_id]
        def list_ids(self):
            return ["main", "support"]

    captured = {}

    class FakeEngine:
        async def run(self, *, messages, system_prompt, tools, on_event, abort,
                      session=None, model=None):
            captured["model"] = model        # support's model identity flows through

    svc = AgentService(
        engine=FakeEngine(), tools=[], registry=TwoAgentRegistry(),
        make_session=lambda sid, agent: FakeSession(),
        build_prompt=lambda tools, agent, mode, query="": captured.setdefault("agent", agent.id) or "SYS",
    )

    async def sink(_e):
        pass

    # session key would resolve to 'main', but the explicit agent_id picks 'support'
    await svc.handle_message("term-xyz", "hi", sink, asyncio.Event(), agent_id="support")
    assert captured["agent"] == "support"


@pytest.mark.asyncio
async def test_per_agent_model_override_reaches_engine():
    engine = FakeEngine()
    spec = AgentSpec(id="support", name="S", workspace=Path("."), state_dir=Path("."),
                     model="gemini/gemini-2.5-flash")
    svc = AgentService(
        engine=engine,
        tools=[],
        registry=FakeRegistry(spec),
        make_session=lambda sid, agent: FakeSession(),
        build_prompt=lambda tools, agent, mode, query="": "SYS",
    )

    async def sink(_ev):
        pass

    await svc.handle_message("agent:support:x", "hi", sink, asyncio.Event())
    assert engine.calls[0]["model"] == "gemini/gemini-2.5-flash"


class _OutcomeEngine:
    """Declares an outcome only on its Nth run — simulates the agent forgetting, then declaring."""
    def __init__(self, declare_on_run):
        self.runs = 0
        self._on = declare_on_run

    async def run(self, *, messages, system_prompt, tools, on_event, abort, session=None, model=None):
        self.runs += 1
        if self.runs == self._on:
            set_run_outcome("done", "all good")
        return []


def _svc(engine, agent_id="job"):
    spec = AgentSpec(id=agent_id, name="J", workspace=Path("."), state_dir=Path("."))
    return AgentService(engine=engine, tools=["report_outcome"], registry=FakeRegistry(spec),
                        make_session=lambda sid, agent: FakeSession(),
                        build_prompt=lambda tools, agent, mode, query="": "SYS")


@pytest.mark.asyncio
async def test_cron_forces_outcome_when_agent_skips_it():
    # agent declares only on the 2nd run -> the forced follow-up makes it happen
    engine = _OutcomeEngine(declare_on_run=2)
    tok = rc._outcome.set(None)
    try:
        await _svc(engine).handle_message("agent:job:x", "do it", _sink, asyncio.Event(),
                                          mode=RunMode.CRON)
        assert engine.runs == 2                          # a 2nd, forcing turn ran
        assert rc.current_run_outcome() == ("done", "all good")
    finally:
        rc._outcome.reset(tok)


@pytest.mark.asyncio
async def test_cron_no_nudge_when_already_declared():
    engine = _OutcomeEngine(declare_on_run=1)            # declares on the first run
    tok = rc._outcome.set(None)
    try:
        await _svc(engine).handle_message("agent:job:x", "do it", _sink, asyncio.Event(),
                                          mode=RunMode.CRON)
        assert engine.runs == 1                          # no follow-up needed
    finally:
        rc._outcome.reset(tok)


@pytest.mark.asyncio
async def test_interactive_run_is_never_forced():
    engine = _OutcomeEngine(declare_on_run=99)           # never declares
    tok = rc._outcome.set(None)
    try:
        await _svc(engine, "main").handle_message("s1", "hi", _sink, asyncio.Event())  # interactive
        assert engine.runs == 1                          # only cron runs are forced
    finally:
        rc._outcome.reset(tok)
