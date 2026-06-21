"""S8/S9 — sub-agent spawn: a child run executes a delegated task and returns its answer;
capped + depth-limited; context-isolated so the child never clobbers the parent."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentd.application.run_context import RunContext, set_run_context
from agentd.domain.messages import AssistantMessage, TextContent
from agentd.infrastructure.memory.local_store import SessionStore


def _reg(tmp_path):
    return SimpleNamespace(get=lambda a: SimpleNamespace(state_dir=tmp_path),
                           list_ids=lambda: ["main"])


@pytest.mark.asyncio
async def test_spawn_runs_child_and_returns_its_answer(tmp_path):
    from agentd.presentation.gateway import Gateway

    class _FakeService:
        async def handle_message(self, session_id, text, on_event, abort, mode=None, agent_id=None):
            assert ":sub:" in session_id and agent_id == "main"
            s = SessionStore(tmp_path, session_id)
            s.load()
            s.append(AssistantMessage(content=[TextContent(text=f"child did: {text}")]))

    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path, subagent_max=4),
                 service=_FakeService(), registry=_reg(tmp_path))
    set_run_context(RunContext("main", "agent:main:dev", "interactive"))   # parent context
    result = await gw._spawn_subagent(None, "summarize the docs")
    assert result == "child did: summarize the docs"
    assert gw.subagent_active == 0                                          # counter released


@pytest.mark.asyncio
async def test_spawn_cap_and_depth_guards(tmp_path):
    from agentd.presentation.gateway import Gateway

    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path, subagent_max=0),
                 service=None, registry=_reg(tmp_path))
    set_run_context(RunContext("main", "agent:main:dev", "interactive"))
    assert "limit reached" in await gw._spawn_subagent(None, "x")           # cap=0

    set_run_context(RunContext("main", "agent:main:sub:abc", "interactive"))
    assert "depth limit" in await gw._spawn_subagent(None, "x")             # a sub can't spawn
