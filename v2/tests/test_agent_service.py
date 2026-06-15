"""Locks the AgentService orchestration with fakes (no IO, no LLM)."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.services.agent_service import AgentService
from agentd.domain.messages import UserMessage


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

    async def run(self, *, messages, system_prompt, tools, on_event, abort, session=None):
        self.calls.append(
            {"messages": list(messages), "system_prompt": system_prompt,
             "tools": tools, "session": session}
        )
        return []


@pytest.mark.asyncio
async def test_handle_message_orchestration():
    sess = FakeSession()
    engine = FakeEngine()
    svc = AgentService(
        engine=engine,
        tools=["read", "exec"],
        make_session=lambda sid: sess,
        build_prompt=lambda tools: f"SYS({len(tools)})",
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
