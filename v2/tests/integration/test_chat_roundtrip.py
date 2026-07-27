"""The chat.send SPINE — the deepest in-process integration we have: a real dispatch
frame drives the real Gateway -> real AgentService -> real NativeEngine (scripted LLM
stream, no network) -> a real Tool executes -> the transcript persists via a real
SessionStore -> chat.event frames reach a captured client. Everything between the
WebSocket and the LLM wire is real; only the model stream is scripted."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.services.agent_service import AgentService
from agentd.domain.messages import AssistantMessage, TextContent, ToolCallContent
from agentd.infrastructure.engine.native import NativeEngine
from agentd.infrastructure.memory.local_store import (
    SessionStore,
    read_session_messages,
    write_session_meta,
)
from agentd.infrastructure.tools import Tool, ToolResult
from agentd.presentation.gateway import Gateway
from agentd.presentation.protocol import Request


class EchoTool(Tool):
    name = "echo"
    description = "Echo back the input."
    label = "Echo"
    parameters = {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    }

    def __init__(self):
        self.calls: list[dict] = []

    async def execute(self, tool_call_id, params, abort, on_update=None):
        self.calls.append(params)
        return ToolResult.text(f"echo: {params['text']}")


def _scripted_stream(script):
    """One list of stream events per LLM call; pops a turn per call (see test_loop)."""
    turns = list(script)

    async def stream_fn(*, model, system_prompt, messages, tools, abort):
        for ev in turns.pop(0):
            yield ev

    return stream_fn


def _hanging_stream():
    async def stream_fn(*, model, system_prompt, messages, tools, abort):
        await asyncio.sleep(30)  # a stalled provider — only an abort gets us out
        yield {}

    return stream_fn


def _text_turn(text):
    return [
        {"type": "text_delta", "delta": text},
        {
            "type": "done",
            "message": AssistantMessage(content=[TextContent(text=text)], stop_reason="stop"),
        },
    ]


def _tool_turn(call_id, name, args):
    block = ToolCallContent(id=call_id, name=name, arguments=args)
    return [
        {"type": "toolcall_end", "toolCall": {"id": call_id, "name": name}},
        {
            "type": "done",
            "message": AssistantMessage(content=[block], stop_reason="toolUse"),
        },
    ]


class _CapturingWs:
    def __init__(self):
        self.frames: list[str] = []

    async def send(self, frame: str) -> None:
        self.frames.append(frame)

    def chat_events(self) -> list[dict]:
        out = []
        for f in self.frames:
            obj = json.loads(f)
            if obj.get("event") == "chat.event":
                out.append(obj.get("payload") or {})
        return out


def _harness(tmp_path, stream_fn, tools=None):
    """The real composition, minus network: Gateway + AgentService + NativeEngine."""
    state_dir = tmp_path / "agents" / "main"
    ws_dir = state_dir / "workspace"
    ws_dir.mkdir(parents=True)
    spec = SimpleNamespace(
        id="main",
        name="Main",
        model=None,
        workspace=ws_dir,
        state_dir=state_dir,
        plugins={},
        tools_allow=None,
        tools_deny=(),
        subagents_allow=None,
    )
    registry = SimpleNamespace(
        list_ids=lambda: ["main"],
        get=lambda a: {"main": spec}[a],
        resolve=lambda _k: spec,
    )
    service = AgentService(
        engine=NativeEngine(stream_fn, model="test/model"),
        tools=list(tools or []),
        registry=registry,
        make_session=lambda sid, agent: SessionStore(state_dir, sid),
        build_prompt=lambda tools, agent, mode, query="": "SYS",
    )
    gw = Gateway(
        config=SimpleNamespace(
            state_dir=tmp_path,
            workspace=ws_dir,
            agent_name="jarvis",
            enforce_outcome=False,
        ),
        service=service,
        registry=registry,
    )
    client = _CapturingWs()
    gw.clients.add(client)
    # pre-title the session so the fire-and-forget auto-titler skips (it would try a model)
    write_session_meta(state_dir, "conv", title="t", manual=True)
    return gw, client, state_dir


async def _send_and_wait(gw, params):
    resp = await gw._dispatch(Request(id="1", method="chat.send", params=params))
    assert resp.ok, resp.payload
    await gw.runs[params.get("sessionKey", "default")].task
    return resp.payload


def test_full_turn_tool_call_events_and_persistence(tmp_path):
    tool = EchoTool()
    stream = _scripted_stream(
        [
            _tool_turn("tc1", "echo", {"text": "ping"}),
            _text_turn("the echo said: ping"),
        ]
    )
    gw, client, state_dir = _harness(tmp_path, stream, tools=[tool])

    payload = asyncio.run(_send_and_wait(gw, {"sessionKey": "conv", "message": "run echo"}))
    assert payload["runId"]

    # the REAL tool ran with the model's arguments
    assert tool.calls == [{"text": "ping"}]

    # the transcript persisted end to end: user -> toolUse assistant -> tool result -> final
    stored = read_session_messages(state_dir, "conv")
    roles = [m.get("role") for m in stored]
    assert roles == ["user", "assistant", "toolResult", "assistant"]
    assert "the echo said: ping" in json.dumps(stored[-1])

    # every live event reached the client, tagged with the run + agent
    events = client.chat_events()
    assert events and all(p["agentId"] == "main" for p in events)
    assert all(p["runId"] == payload["runId"] for p in events)
    types = [p["event"].get("type") for p in events]
    assert "agent_end" in types  # the turn closed out on the wire


def test_idempotency_key_dedupes_a_retry(tmp_path):
    stream = _scripted_stream([_text_turn("done once")])
    gw, _client, _sd = _harness(tmp_path, stream)

    async def scenario():
        first = await _send_and_wait(
            gw, {"sessionKey": "conv", "message": "hi", "idempotencyKey": "K1"}
        )
        # the retry (same key) must NOT start a second run — same runId, flagged
        again = await gw._dispatch(
            Request(
                id="2",
                method="chat.send",
                params={"sessionKey": "conv", "message": "hi", "idempotencyKey": "K1"},
            )
        )
        assert again.ok
        assert again.payload["deduplicated"] is True
        assert again.payload["runId"] == first["runId"]

    asyncio.run(scenario())


def test_busy_session_rejects_a_second_send(tmp_path):
    gw, _client, _sd = _harness(tmp_path, _hanging_stream())

    async def scenario():
        started = await gw._dispatch(
            Request(id="1", method="chat.send", params={"sessionKey": "conv", "message": "go"})
        )
        assert started.ok
        busy = await gw._dispatch(
            Request(id="2", method="chat.send", params={"sessionKey": "conv", "message": "again"})
        )
        assert busy.ok is False and "active run" in busy.payload["error"]
        # cleanup: abort the hung run
        await gw._dispatch(Request(id="3", method="chat.abort", params={"sessionKey": "conv"}))
        task = gw.runs["conv"].task
        await asyncio.wait([task])

    asyncio.run(scenario())


def test_abort_stops_a_stalled_run(tmp_path):
    gw, _client, _sd = _harness(tmp_path, _hanging_stream())

    async def scenario():
        started = await gw._dispatch(
            Request(id="1", method="chat.send", params={"sessionKey": "conv", "message": "go"})
        )
        assert started.ok
        aborted = await gw._dispatch(
            Request(id="2", method="chat.abort", params={"sessionKey": "conv"})
        )
        assert aborted.payload["aborted"] is True
        assert aborted.payload["runId"] == started.payload["runId"]
        task = gw.runs["conv"].task
        await asyncio.wait([task])
        assert task.done()
        # a second abort finds nothing running
        idle = await gw._dispatch(
            Request(id="3", method="chat.abort", params={"sessionKey": "conv"})
        )
        assert idle.payload["aborted"] is False

    asyncio.run(scenario())


def test_bad_sends_surface_clean_errors(tmp_path):
    gw, _client, _sd = _harness(tmp_path, _scripted_stream([]))

    async def scenario():
        empty = await gw._dispatch(
            Request(id="1", method="chat.send", params={"sessionKey": "conv", "message": "  "})
        )
        assert empty.ok is False and "must not be empty" in empty.payload["error"]
        ghost = await gw._dispatch(
            Request(
                id="2",
                method="chat.send",
                params={"sessionKey": "conv", "message": "hi", "agentId": "ghost"},
            )
        )
        assert ghost.ok is False and "unknown agent" in ghost.payload["error"]
        assert gw.runs == {}  # nothing started

    asyncio.run(scenario())
