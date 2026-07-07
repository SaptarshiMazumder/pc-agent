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
        async def handle_message(self, session_id, text, on_event, abort, mode=None, agent_id=None,
                                 attachments=None):
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
    assert "depth" in await gw._spawn_subagent(None, "x")                   # a sub can't spawn (default depth 1)


@pytest.mark.asyncio
async def test_subagent_depth_configurable(tmp_path):
    """A3: with subagent_max_depth=2, a depth-1 sub may spawn; a depth-2 one may not."""
    from agentd.presentation.gateway import Gateway, _subagent_depth

    assert _subagent_depth("agent:main:dev") == 0
    assert _subagent_depth("agent:main:sub:1:abc") == 1
    assert _subagent_depth("agent:main:sub:2:abc") == 2
    assert _subagent_depth("agent:main:sub:abc") == 1          # legacy flat key

    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path, subagent_max=0, subagent_max_depth=2),
                 service=None, registry=_reg(tmp_path))
    set_run_context(RunContext("main", "agent:main:sub:1:abc", "interactive"))   # depth 1 < 2
    assert "limit reached" in await gw._spawn_subagent(None, "x")          # past depth -> hits cap
    set_run_context(RunContext("main", "agent:main:sub:2:abc", "interactive"))   # depth 2 == max
    assert "depth" in await gw._spawn_subagent(None, "x")                  # blocked at max depth


@pytest.mark.asyncio
async def test_subagent_allowlist_blocks_unscoped_target(tmp_path):
    """A4: a caller with [subagents] allow may only delegate to matching ids/globs."""
    from agentd.presentation.gateway import Gateway

    reg = SimpleNamespace(
        get=lambda a: SimpleNamespace(state_dir=tmp_path, subagents_allow=("check-*",)),
        list_ids=lambda: ["main", "check-vuln", "random-agent"])
    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path, subagent_max=4, subagent_max_depth=1),
                 service=None, registry=reg)
    set_run_context(RunContext("code-review", "agent:code-review:dev", "interactive"))
    assert "may not delegate" in await gw._spawn_subagent("random-agent", "x")   # not in allow
    # "check-vuln" matches "check-*" -> passes the allowlist (proceeds past the guard)


@pytest.mark.asyncio
async def test_message_agent_guards(tmp_path):
    """A5: message_agent honors self / unknown / allowlist / loop guards (all pre-run)."""
    from agentd.presentation.gateway import Gateway

    reg = SimpleNamespace(
        get=lambda a: SimpleNamespace(subagents_allow=("check-*",)),
        list_ids=lambda: ["main", "check-vuln", "code-review", "random-agent"])
    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path), service=None, registry=reg)

    set_run_context(RunContext("code-review", "agent:code-review:dev", "interactive"))
    assert "cannot message yourself" in await gw._message_agent("code-review", "hi")
    assert "unknown agent" in await gw._message_agent("ghost", "hi")
    assert "may not message" in await gw._message_agent("random-agent", "hi")    # not in check-*

    set_run_context(RunContext("code-review", "agent:code-review:peer:main", "interactive"))
    assert "loop guard" in await gw._message_agent("check-vuln", "hi")           # no chaining


def test_subagent_relay_compacts_meaningful_beats_only():
    # the PARENT-view relay: only start / tool / done / error become a compact subagent_event;
    # raw text/thinking deltas (and tool-end) are dropped so the parent isn't flooded.
    from agentd.domain.events import AgentEvent
    from agentd.presentation.gateway import subagent_relay

    ck = "agent:scout:sub:abcd1234"        # child session key -> agent resolved as "scout"
    start = subagent_relay(ck, AgentEvent("agent_start", {}))
    assert start.type == "subagent_event"
    assert start.payload == {"childAgent": "scout", "kind": "start"}

    tool = subagent_relay(ck, AgentEvent("tool_execution_start", {"toolName": "exec", "args": {}}))
    assert tool.payload == {"childAgent": "scout", "kind": "tool", "tool": "exec"}

    done = subagent_relay(ck, AgentEvent("agent_end", {"stopReason": "stop"}))
    assert done.payload == {"childAgent": "scout", "kind": "done", "detail": "stop"}

    err = subagent_relay(ck, AgentEvent("agent_end", {"stopReason": "error", "error": "boom"}))
    assert err.payload == {"childAgent": "scout", "kind": "error", "detail": "boom"}

    assert subagent_relay(ck, AgentEvent("message_update",
                                         {"kind": "text_delta", "delta": "hi"})) is None
    assert subagent_relay(ck, AgentEvent("tool_execution_end", {"toolName": "exec"})) is None
