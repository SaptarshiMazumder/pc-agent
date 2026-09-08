"""The e2e gate, driven through the REAL agent loop.

The gate's own unit behaviour is a state machine and easy to assert. What actually had to be
proved is the thing a state machine cannot tell you: that a halt returned from `on_turn` reaches
the loop, becomes a steering message, and STOPS THE MODEL FINISHING -- and that it does so on the
finishing turn rather than in the middle of a build.

That distinction is the whole design. The loop notifies observers at two places (after a round of
tool calls, and again when the model produced none), and a gate that fired at the first would
interrupt work being done correctly. These tests drive the loop with a scripted model so both
paths are exercised for real.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents/agent-builder/plugins/agent-authoring"))

from agent_authoring.domain.e2e_gate import E2eGate

from agent_runtime.domain.events import AgentEvent
from agent_runtime.domain.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    UserMessage,
)
from agent_runtime.infrastructure.engine.native import run_agent_loop
from agent_runtime.infrastructure.tools import Tool, ToolResult


class _Named(Tool):
    """A tool that answers to whatever name the script calls it by."""

    description = "test double"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, name):
        self.name = name
        self.label = name

    async def execute(self, tool_call_id, params, abort, on_update=None):
        return ToolResult.text("ok")


def _stream(script):
    turns = list(script)

    async def stream_fn(*, model, system_prompt, messages, tools, abort):
        for ev in turns.pop(0):
            yield ev

    return stream_fn


def _text(t):
    return [{"type": "done",
             "message": AssistantMessage(content=[TextContent(text=t)], stop_reason="stop")}]


def _call(name, call_id, args=None):
    b = ToolCallContent(id=call_id, name=name, arguments=args or {})
    return [{"type": "toolcall_end", "toolCall": {"id": b.id, "name": b.name}},
            {"type": "done", "message": AssistantMessage(content=[b], stop_reason="toolUse")}]


async def _run(script, names):
    events, msgs = [], [UserMessage(content="build me an agent")]

    async def on_event(ev: AgentEvent):
        events.append(ev)

    await run_agent_loop(
        messages=msgs,
        system_prompt="sys",
        tools=[_Named(n) for n in names],
        stream_fn=_stream(script),
        model="fake",
        on_event=on_event,
        abort=asyncio.Event(),
        observers=[E2eGate()],
    )
    return events, msgs


def _steered(msgs):
    return [m for m in msgs if m.role == "user" and str(m.content).startswith("[liveness]")]


@pytest.mark.asyncio
async def test_an_untested_batch_cannot_finish():
    """The failure this exists for: build something, declare it working, never test it."""
    script = [
        _call("create_tool", "c1", {"name": "count_words"}),
        _call("write", "c2", {"path": "/agents/word-count/plugins/wc/wc.py"}),
        _text("Word Count is built and working -- try it out"),   # the claim that started this
        # Everything past here is only reached because the gate refused that claim: the model is
        # sent round again, does the testing it skipped, and only then gets to finish.
        _call("e2e_run", "c3", {"scenario_path": "agents/word-count/e2e/basic.json"}),
        _text("Tested -- the scenario passes"),
    ]
    events, msgs = await _run(script, ["create_tool", "write", "e2e_run"])

    steer = _steered(msgs)
    assert steer, "the model was allowed to finish an untested batch"
    assert "not proved" in steer[0].content
    assert "ASK THEM FOR IT" in steer[0].content, "must tell it to ask for what a test needs"
    assert [e for e in events if e.type == "continuation"], "no continuation was emitted"
    # It was steered ONCE and released as soon as it complied -- not nagged forever.
    assert len(steer) == 1, f"expected one nudge, got {len(steer)}"
    assert msgs[-1].text.startswith("Tested"), "the model never got to finish after complying"


@pytest.mark.asyncio
async def test_a_tested_batch_finishes_untouched():
    """The other half: having tested it, the model is left alone."""
    script = [
        _call("create_tool", "c1", {"name": "count_words"}),
        _call("write", "c2", {"path": "/agents/word-count/plugins/wc/wc.py"}),
        _call("e2e_run", "c3", {"scenario_path": "agents/word-count/e2e/basic.json"}),
        _text("Word Count is built, and the scenario passes"),
    ]
    _events, msgs = await _run(script, ["create_tool", "write", "e2e_run"])
    assert not _steered(msgs), "a tested batch must not be nagged"
    assert msgs[-1].text.startswith("Word Count is built")


@pytest.mark.asyncio
async def test_it_stays_quiet_while_the_build_is_still_going():
    """Mid-build is not a batch boundary. A gate that fired here would interrupt correct work."""
    script = [
        _call("create_tool", "c1", {"name": "a"}),
        _call("write", "c2", {"path": "/agents/x/plugins/p/a.py"}),
        _call("write", "c3", {"path": "/agents/x/plugins/p/b.py"}),
        _call("e2e_run", "c4", {}),
        _text("done, and tested"),
    ]
    _events, msgs = await _run(script, ["create_tool", "write", "e2e_run"])
    assert not _steered(msgs), "the gate interrupted a build that was still in progress"


@pytest.mark.asyncio
async def test_the_users_waiver_is_accepted():
    """skip_e2e is the one door out -- and it is the USER's to open, not the model's judgement."""
    script = [
        _call("create_tool", "c1", {"name": "a"}),
        _call("write", "c2", {"path": "/agents/x/plugins/p/a.py"}),
        _call("skip_e2e", "c3", {"reason": "user said skip, no instance running"}),
        _text("Built. Tests skipped at your request."),
    ]
    _events, msgs = await _run(script, ["create_tool", "write", "skip_e2e"])
    assert not _steered(msgs), "a user waiver must stand the gate down"


@pytest.mark.asyncio
async def test_other_agents_are_never_touched():
    """The gate is global by wiring but must be inert unless an AUTHORING tool armed it."""
    script = [
        _call("write", "c1", {"path": "/some/notes.md"}),
        _text("wrote your notes"),
    ]
    _events, msgs = await _run(script, ["write"])
    assert not _steered(msgs), "the gate fired on a run that was not building an agent"
