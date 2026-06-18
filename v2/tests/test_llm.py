"""Tests for LiteLLM message conversion and tool-call delta assembly
(mocked chunks; no network)."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.llm.litellm import (
    _ToolCallAccumulator,
    litellm_stream,
    messages_to_litellm,
)
from agentd.domain.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)


def test_messages_to_litellm_conversion():
    history = [
        UserMessage(content="hi"),
        AssistantMessage(
            content=[
                ThinkingContent(thinking="pondering"),
                TextContent(text="let me check"),
                ToolCallContent(id="tc1", name="exec", arguments={"command": "ls"}),
            ],
            stop_reason="toolUse",
        ),
        ToolResultMessage(
            tool_call_id="tc1", tool_name="exec", content=[TextContent(text="a.txt")]
        ),
        AssistantMessage(content=[TextContent(text="found a.txt")], stop_reason="stop"),
    ]
    out = messages_to_litellm("SYS", history)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "hi"}
    a = out[2]
    assert a["role"] == "assistant"
    assert a["content"] == "let me check"  # thinking dropped
    assert a["tool_calls"][0]["id"] == "tc1"
    assert json.loads(a["tool_calls"][0]["function"]["arguments"]) == {"command": "ls"}
    t = out[3]
    assert t == {"role": "tool", "tool_call_id": "tc1", "content": "a.txt"}
    assert out[4] == {"role": "assistant", "content": "found a.txt"}


def test_error_tool_result_marked():
    out = messages_to_litellm(
        "S",
        [
            ToolResultMessage(
                tool_call_id="x",
                tool_name="exec",
                content=[TextContent(text="boom")],
                is_error=True,
            )
        ],
    )
    assert out[1]["content"] == "ERROR: boom"


def frag(index=None, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_accumulator_assembles_by_index():
    acc = _ToolCallAccumulator()
    acc.feed([frag(index=0, id="call_a", name="exec", arguments='{"comm')])
    acc.feed([frag(index=0, arguments='and": "ls"}')])
    acc.feed([frag(index=1, id="call_b", name="read", arguments='{"path": "x.txt"}')])
    calls = acc.finish()
    assert len(calls) == 2
    assert calls[0].id == "call_a"
    assert calls[0].arguments == {"command": "ls"}
    assert calls[1].name == "read"
    assert calls[1].arguments == {"path": "x.txt"}


def test_accumulator_handles_none_index_and_empty_shards():
    acc = _ToolCallAccumulator()
    acc.feed([frag(index=None, id="c1", name="echo", arguments=None)])
    acc.feed([frag(index=None, arguments="")])
    acc.feed([frag(index=None, arguments='{"text": "hi"}')])
    calls = acc.finish()
    assert len(calls) == 1
    assert calls[0].arguments == {"text": "hi"}


def test_accumulator_bad_json_kept_raw():
    acc = _ToolCallAccumulator()
    acc.feed([frag(index=0, id="c1", name="x", arguments="{not json")])
    calls = acc.finish()
    assert calls[0].arguments == {"_raw": "{not json"}


def make_chunk(delta=None, finish_reason=None, usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)] if delta or finish_reason else [],
        usage=usage,
    )


def make_delta(content=None, tool_calls=None, reasoning_content=None):
    return SimpleNamespace(
        content=content, tool_calls=tool_calls, reasoning_content=reasoning_content
    )


class FakeStream:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.chunks:
            raise StopAsyncIteration
        return self.chunks.pop(0)


@pytest.mark.asyncio
async def test_litellm_stream_text_and_toolcall(monkeypatch):
    chunks = [
        make_chunk(delta=make_delta(content="I'll check. ")),
        make_chunk(
            delta=make_delta(
                tool_calls=[frag(index=0, id="c9", name="exec", arguments='{"command"')]
            )
        ),
        make_chunk(delta=make_delta(tool_calls=[frag(index=0, arguments=': "dir"}')])),
        make_chunk(delta=make_delta(), finish_reason="tool_calls"),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7)),
    ]

    async def fake_acompletion(**kwargs):
        return FakeStream(chunks)

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    events = []
    async for ev in litellm_stream(
        model="fake/model",
        system_prompt="S",
        messages=[UserMessage(content="list files")],
        tools=[],
        abort=asyncio.Event(),
    ):
        events.append(ev)

    assert events[0] == {"type": "text_delta", "delta": "I'll check. "}
    toolcall_evs = [e for e in events if e["type"] == "toolcall_end"]
    assert toolcall_evs[0]["toolCall"]["name"] == "exec"
    assert toolcall_evs[0]["toolCall"]["arguments"] == {"command": "dir"}
    done = events[-1]
    assert done["type"] == "done"
    msg = done["message"]
    assert msg.stop_reason == "toolUse"
    assert msg.usage == {"input": 12, "output": 7}
    assert msg.text == "I'll check. "


@pytest.mark.asyncio
async def test_litellm_stream_provider_error_never_raises(monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("provider down")

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    events = []
    async for ev in litellm_stream(
        model="fake/model",
        system_prompt="S",
        messages=[UserMessage(content="hi")],
        tools=[],
        abort=asyncio.Event(),
    ):
        events.append(ev)

    done = events[-1]
    assert done["type"] == "done"
    assert done["message"].stop_reason == "error"
    assert "provider down" in done["message"].error_message


class StallingStream:
    """A stream whose chunks never arrive (simulates a hung/silent model)."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()  # blocks forever


@pytest.mark.asyncio
async def test_idle_timeout_emits_error_done(monkeypatch):
    async def fake_acompletion(**kwargs):
        return StallingStream()

    import litellm
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    events = []
    async for ev in litellm_stream(
        model="gemini/gemini-3.1-pro-preview", system_prompt="S",
        messages=[UserMessage(content="hi")], tools=[], abort=asyncio.Event(),
        idle_timeout_sec=0.05,
    ):
        events.append(ev)

    done = events[-1]
    assert done["type"] == "done" and done["message"].stop_reason == "error"
    assert "idle timeout" in done["message"].error_message


@pytest.mark.asyncio
async def test_request_timeout_passed_to_acompletion(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return FakeStream([
            make_chunk(delta=make_delta(content="hi")),
            make_chunk(delta=make_delta(), finish_reason="stop"),
        ])

    import litellm
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    async for _ in litellm_stream(
        model="gemini/x", system_prompt="S", messages=[UserMessage(content="hi")],
        tools=[], abort=asyncio.Event(), request_timeout_sec=99,
    ):
        pass
    assert captured.get("request_timeout") == 99


@pytest.mark.asyncio
async def test_local_provider_skips_idle(monkeypatch):
    # local model + a stalling stream: idle is SKIPPED, so no idle error fires —
    # the stream stays open. We prove it by timing the consumer out ourselves.
    async def fake_acompletion(**kwargs):
        return StallingStream()

    import litellm
    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    async def consume():
        async for _ in litellm_stream(
            model="ollama/llama3", system_prompt="S", messages=[UserMessage(content="hi")],
            tools=[], abort=asyncio.Event(), idle_timeout_sec=0.05,
        ):
            pass

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(consume(), timeout=0.3)  # never emits idle error -> we time out
