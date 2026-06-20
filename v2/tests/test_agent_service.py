"""Locks the AgentService orchestration with fakes (no IO, no LLM)."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pathlib import Path

from agentd.application.services.agent_service import AgentService
from agentd.domain.agent import AgentSpec
from agentd.domain.messages import UserMessage


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
        build_prompt=lambda tools, agent, mode: f"SYS({len(tools)})",
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
        build_prompt=lambda tools, agent, mode: captured.setdefault("agent", agent.id) or "SYS",
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
        build_prompt=lambda tools, agent, mode: "SYS",
    )

    async def sink(_ev):
        pass

    await svc.handle_message("agent:support:x", "hi", sink, asyncio.Event())
    assert engine.calls[0]["model"] == "gemini/gemini-2.5-flash"
