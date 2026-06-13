import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.events import AgentEvent
from agentd.loop import run_agent_loop
from agentd.tools import Tool, ToolResult
from agentd.types import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    UserMessage,
)


class EchoTool(Tool):
    name = "echo"
    description = "Echo back the input."
    label = "Echo"
    parameters = {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        return ToolResult.text(f"echo: {params['text']}")


class SlowTool(Tool):
    name = "slow"
    description = "Sleeps then returns."
    label = "Slow"
    parameters = {"type": "object", "properties": {"ms": {"type": "number"}}}

    def __init__(self):
        self.started = []
        self.finished = []

    async def execute(self, tool_call_id, params, abort, on_update=None):
        self.started.append(tool_call_id)
        await asyncio.sleep(params.get("ms", 10) / 1000)
        self.finished.append(tool_call_id)
        return ToolResult.text("done")


class BoomTool(Tool):
    name = "boom"
    description = "Always raises."
    label = "Boom"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        raise RuntimeError("kaboom")


def make_stream_fn(script):
    """script: list of turns; each turn is a list of stream events.
    The fake pops one turn per LLM call."""
    turns = list(script)

    async def stream_fn(*, model, system_prompt, messages, tools, abort):
        turn = turns.pop(0)
        for ev in turn:
            yield ev

    return stream_fn


def text_turn(text, stop_reason="stop"):
    return [
        {"type": "text_delta", "delta": text},
        {
            "type": "done",
            "message": AssistantMessage(
                content=[TextContent(text=text)], stop_reason=stop_reason
            ),
        },
    ]


def tool_turn(calls):
    blocks = [ToolCallContent(id=i, name=n, arguments=a) for i, n, a in calls]
    evs = [{"type": "toolcall_end", "toolCall": {"id": b.id, "name": b.name}} for b in blocks]
    evs.append(
        {
            "type": "done",
            "message": AssistantMessage(content=blocks, stop_reason="toolUse"),
        }
    )
    return evs


async def collect_run(script, tools, messages=None):
    events = []

    async def on_event(ev: AgentEvent):
        events.append(ev)

    msgs = messages if messages is not None else [UserMessage(content="go")]
    new = await run_agent_loop(
        messages=msgs,
        system_prompt="sys",
        tools=tools,
        stream_fn=make_stream_fn(script),
        model="fake",
        on_event=on_event,
        abort=asyncio.Event(),
    )
    return events, new, msgs


@pytest.mark.asyncio
async def test_simple_text_run():
    events, new, msgs = await collect_run([text_turn("hello!")], [])
    types = [e.type for e in events]
    assert types == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert events[-1].payload["stopReason"] == "stop"
    assert len(new) == 1 and new[0].text == "hello!"


@pytest.mark.asyncio
async def test_tool_call_then_answer():
    script = [
        tool_turn([("tc1", "echo", {"text": "hi"})]),
        text_turn("the echo said hi"),
    ]
    events, new, msgs = await collect_run(script, [EchoTool()])
    types = [e.type for e in events]
    assert types == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_update",       # toolcall block
        "message_end",          # assistant w/ tool call
        "tool_execution_start",
        "tool_execution_end",
        "message_end",          # toolResult
        "turn_end",
        "turn_start",
        "message_start",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    # history: user, assistant(toolUse), toolResult, assistant(stop)
    assert [m.role for m in msgs] == ["user", "assistant", "toolResult", "assistant"]
    assert msgs[2].text == "echo: hi"
    assert msgs[2].is_error is False


@pytest.mark.asyncio
async def test_parallel_execution_and_source_order():
    slow = SlowTool()
    script = [
        tool_turn([("a", "slow", {"ms": 80}), ("b", "slow", {"ms": 10})]),
        text_turn("done"),
    ]
    events, new, msgs = await collect_run(script, [slow])
    # both started before either finished => actually concurrent
    assert slow.finished == ["b", "a"]
    # tool results appended in assistant source order regardless of finish order
    tool_results = [m for m in msgs if m.role == "toolResult"]
    assert [m.tool_call_id for m in tool_results] == ["a", "b"]


@pytest.mark.asyncio
async def test_sequential_tool_runs_in_order():
    slow = SlowTool()
    slow.concurrency = "sequential"
    script = [
        tool_turn([("a", "slow", {"ms": 50}), ("b", "slow", {"ms": 5})]),
        text_turn("done"),
    ]
    await collect_run(script, [slow])
    assert slow.finished == ["a", "b"]


@pytest.mark.asyncio
async def test_tool_exception_becomes_error_result():
    script = [
        tool_turn([("tc1", "boom", {})]),
        text_turn("recovered"),
    ]
    events, new, msgs = await collect_run(script, [BoomTool()])
    tr = [m for m in msgs if m.role == "toolResult"][0]
    assert tr.is_error is True
    assert "kaboom" in tr.text
    assert events[-1].payload["stopReason"] == "stop"


@pytest.mark.asyncio
async def test_invalid_args_rejected_without_execution():
    script = [
        tool_turn([("tc1", "echo", {"wrong": 1})]),
        text_turn("ok"),
    ]
    events, new, msgs = await collect_run(script, [EchoTool()])
    tr = [m for m in msgs if m.role == "toolResult"][0]
    assert tr.is_error is True
    assert "Invalid arguments" in tr.text


@pytest.mark.asyncio
async def test_unknown_tool():
    script = [
        tool_turn([("tc1", "nope", {})]),
        text_turn("ok"),
    ]
    events, new, msgs = await collect_run(script, [])
    tr = [m for m in msgs if m.role == "toolResult"][0]
    assert tr.is_error is True
    assert "Unknown tool" in tr.text


@pytest.mark.asyncio
async def test_stream_error_ends_run():
    script = [
        [
            {
                "type": "done",
                "message": AssistantMessage(stop_reason="error", error_message="provider down"),
            }
        ]
    ]
    events, new, msgs = await collect_run(script, [])
    assert events[-1].payload["stopReason"] == "error"
