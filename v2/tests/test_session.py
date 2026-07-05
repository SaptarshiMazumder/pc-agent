import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.memory.local_store import SessionStore, list_sessions
from agentd.domain.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
    message_from_dict,
    message_to_dict,
)


def test_agent_session_key_with_colons_is_filesystem_safe(tmp_path):
    # agent:<id>:<peer> keys contain ':' (illegal in a Windows filename) — the store
    # must sanitize the PATH while keeping the real key in the header.
    store = SessionStore(tmp_path, "agent:spending-agent:dev")
    assert store.load() == []                          # creates the file, no crash
    store.append(UserMessage(content="hi"))
    assert ":" not in store.path.name                  # filename sanitized
    reloaded = SessionStore(tmp_path, "agent:spending-agent:dev").load()
    assert len(reloaded) == 1 and reloaded[0].content == "hi"
    header = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
    assert header["id"] == "agent:spending-agent:dev"  # real key preserved in header


def test_message_roundtrip():
    msgs = [
        UserMessage(content="hello"),
        AssistantMessage(
            content=[
                TextContent(text="hi"),
                ToolCallContent(id="tc1", name="exec", arguments={"command": "ls"}),
            ],
            stop_reason="toolUse",
            usage={"input": 10, "output": 5},
            model="test-model",
        ),
        ToolResultMessage(
            tool_call_id="tc1",
            tool_name="exec",
            content=[TextContent(text="file.txt")],
        ),
    ]
    for m in msgs:
        d = message_to_dict(m)
        json.dumps(d)  # must be JSON-serializable
        m2 = message_from_dict(d)
        assert message_to_dict(m2) == d


def test_session_store_roundtrip(tmp_path):
    store = SessionStore(tmp_path, "sess1", cwd="/work")
    assert store.load() == []
    store.append(UserMessage(content="hello"))
    store.append(
        AssistantMessage(content=[TextContent(text="hi there")], stop_reason="stop")
    )

    # fresh store replays both messages
    store2 = SessionStore(tmp_path, "sess1")
    loaded = store2.load()
    assert len(loaded) == 2
    assert loaded[0].role == "user"
    assert loaded[0].content == "hello"
    assert loaded[1].role == "assistant"
    assert loaded[1].text == "hi there"

    # header is the first line, parentId chains
    lines = [json.loads(l) for l in store.path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "session"
    assert lines[0]["version"] == 3
    assert lines[1]["parentId"] is None
    assert lines[2]["parentId"] == lines[1]["id"]

    # appending to the replayed store continues the chain
    store2.append(UserMessage(content="again"))
    lines = [json.loads(l) for l in store.path.read_text(encoding="utf-8").splitlines()]
    assert lines[3]["parentId"] == lines[2]["id"]


def test_list_sessions(tmp_path):
    assert list_sessions(tmp_path) == []
    store = SessionStore(tmp_path, "sessA", cwd="x")
    store.load()
    store.append(UserMessage(content="hi"))
    sessions = list_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0]["sessionId"] == "sessA"
    assert sessions[0]["messages"] == 1


def test_read_session_messages(tmp_path):
    from agentd.infrastructure.memory.local_store import read_session_messages
    from agentd.domain.messages import ToolCallContent, ToolResultMessage

    # non-existent session: [] and NO file created (read-only)
    assert read_session_messages(tmp_path, "ghost") == []
    assert not (tmp_path / "sessions" / "ghost.jsonl").exists()

    store = SessionStore(tmp_path, "sessH")
    store.load()
    store.append(UserMessage(content="hi"))
    store.append(AssistantMessage(content=[
        TextContent(text="on it"),
        ToolCallContent(id="c1", name="read", arguments={"path": "cv.docx"}),
    ], stop_reason="toolUse"))
    store.append(ToolResultMessage(tool_call_id="c1", tool_name="read",
                                   content=[TextContent(text="file body")]))

    msgs = read_session_messages(tmp_path, "sessH")
    assert [m["role"] for m in msgs] == ["user", "assistant", "toolResult"]
    assert msgs[1]["content"][1]["type"] == "toolCall"
    assert msgs[1]["content"][1]["id"] == "c1"
    assert msgs[2]["toolCallId"] == "c1" and msgs[2]["content"][0]["text"] == "file body"
    # every message carries its stored send time (ISO) so clients can show WHEN
    assert all(m.get("ts", "").count("-") >= 2 for m in msgs)


def test_delete_session(tmp_path):
    from agentd.infrastructure.memory.local_store import (
        delete_session,
        read_session_meta,
        write_session_meta,
    )

    store = SessionStore(tmp_path, "gone")
    store.load()
    write_session_meta(tmp_path, "gone", title="Bye")
    assert delete_session(tmp_path, "gone") is True
    assert not store.path.exists()
    assert read_session_meta(tmp_path, "gone") == {}       # sidecar removed too
    assert delete_session(tmp_path, "gone") is False       # already gone


def test_gateway_sessions_delete(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from agentd.presentation.gateway import Gateway

    SessionStore(tmp_path, "d1").load()
    events = []

    class _WS:
        async def send(self, frame):
            events.append(frame)

    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path), service=None,
                 registry=SimpleNamespace(get=lambda a: SimpleNamespace(state_dir=tmp_path)))
    gw.clients = {_WS()}

    out = asyncio.run(gw._sessions_delete({"sessionKey": "d1", "agentId": "main"}))
    assert out["ok"] and out["deleted"]
    assert not (tmp_path / "sessions" / "d1.jsonl").exists()
    assert any("sessions.changed" in f for f in events), "delete must broadcast"

    # deleting a non-existent session is ok=True/deleted=False (idempotent)
    out = asyncio.run(gw._sessions_delete({"sessionKey": "d1", "agentId": "main"}))
    assert out["ok"] and not out["deleted"]
    # missing key -> clear error
    assert not asyncio.run(gw._sessions_delete({}))["ok"]


def test_session_meta_and_titled_list(tmp_path):
    from agentd.infrastructure.memory.local_store import (
        list_sessions,
        read_session_meta,
        write_session_meta,
    )

    store = SessionStore(tmp_path, "chatX")
    store.load()
    store.append(UserMessage(content="How do I center a div in CSS, step by step?"))
    store.append(AssistantMessage(content=[TextContent(text="Use flexbox…")], stop_reason="stop"))

    # no stored title yet -> falls back to a snippet of the first user message
    rows = {s["sessionId"]: s for s in list_sessions(tmp_path)}
    assert rows["chatX"]["title"].startswith("How do I center a div")
    assert rows["chatX"]["titleManual"] is False

    # a stored title wins; manual flag round-trips
    write_session_meta(tmp_path, "chatX", title="Centering a div", manual=True)
    assert read_session_meta(tmp_path, "chatX") == {"title": "Centering a div", "manual": True}
    rows = {s["sessionId"]: s for s in list_sessions(tmp_path)}
    assert rows["chatX"]["title"] == "Centering a div" and rows["chatX"]["titleManual"] is True


def test_gateway_sessions_rename(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from agentd.presentation.gateway import Gateway

    SessionStore(tmp_path, "s1").load()
    events = []

    class _WS:
        async def send(self, frame):
            events.append(frame)

    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path), service=None,
                 registry=SimpleNamespace(get=lambda a: SimpleNamespace(state_dir=tmp_path)))
    gw.clients = {_WS()}

    out = asyncio.run(gw._sessions_rename({"sessionKey": "s1", "agentId": "main", "title": "  My Chat  "}))
    assert out["ok"] and out["title"] == "My Chat"
    from agentd.infrastructure.memory.local_store import read_session_meta
    assert read_session_meta(tmp_path, "s1") == {"title": "My Chat", "manual": True}
    assert any("sessions.changed" in f for f in events), "rename must broadcast sessions.changed"

    # empty title clears the manual name (auto-titling can take over again)
    asyncio.run(gw._sessions_rename({"sessionKey": "s1", "agentId": "main", "title": ""}))
    assert read_session_meta(tmp_path, "s1") == {"title": "", "manual": False}


def test_gateway_sessions_history_is_agent_scoped(tmp_path):
    from types import SimpleNamespace

    from agentd.presentation.gateway import Gateway

    main_dir = tmp_path
    sp_dir = tmp_path / "agents" / "spending-agent"
    SessionStore(sp_dir, "term-sp1").load()
    SessionStore(sp_dir, "term-sp1").append(UserMessage(content="what did I spend"))

    class _Reg:
        _dirs = {"main": main_dir, "spending-agent": sp_dir}

        def get(self, aid):
            if aid not in self._dirs:
                raise KeyError(aid)
            return SimpleNamespace(state_dir=self._dirs[aid])

    gw = Gateway(config=SimpleNamespace(state_dir=main_dir), service=None, registry=_Reg())

    hist = gw._sessions_history({"sessionKey": "term-sp1", "agentId": "spending-agent"})
    assert [m["role"] for m in hist["messages"]] == ["user"]
    assert hist["messages"][0]["content"] == "what did I spend"

    # missing key / wrong agent -> empty, never crashes
    assert gw._sessions_history({})["messages"] == []
    assert gw._sessions_history({"sessionKey": "term-sp1", "agentId": "main"})["messages"] == []


def test_sessions_history_trims_images_and_big_results(tmp_path):
    from types import SimpleNamespace

    from agentd.domain.messages import ImageContent, ToolCallContent, ToolResultMessage
    from agentd.presentation.gateway import Gateway

    store = SessionStore(tmp_path, "big")
    store.load()
    store.append(UserMessage(content="make a figure"))
    store.append(AssistantMessage(content=[
        TextContent(text="here"),
        ImageContent(data="A" * 500_000, mime_type="image/png"),   # a fat inline image
        ToolCallContent(id="c1", name="write", arguments={"content": "Z" * 50_000}),
    ], stop_reason="toolUse"))
    store.append(ToolResultMessage(tool_call_id="c1", tool_name="write",
                                   content=[TextContent(text="Q" * 200_000)]))

    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path), service=None,
                 registry=SimpleNamespace(get=lambda a: SimpleNamespace(state_dir=tmp_path)))
    out = gw._sessions_history({"sessionKey": "big", "agentId": "main"})
    import json
    wire = json.dumps(out)
    assert len(wire) < 100_000, f"trimmed history must be small, got {len(wire)} bytes"
    asst = out["messages"][1]
    img = next(b for b in asst["content"] if b["type"] == "image")
    assert img["data"] == "" and img["elided"] is True          # image bytes dropped
    call = next(b for b in asst["content"] if b["type"] == "toolCall")
    assert len(call["arguments"]["content"]) < 3000             # big arg capped
    result = out["messages"][2]["content"][0]["text"]
    assert len(result) < 5000 and "chars]" in result            # tool result capped w/ marker


def test_gateway_sessions_list_is_agent_scoped(tmp_path):
    # Each agent partitions its own transcripts; sessions.list must return the
    # CALLING agent's threads (so you can resume the right one), not the default's.
    from types import SimpleNamespace

    from agentd.presentation.gateway import Gateway

    main_dir = tmp_path
    sp_dir = tmp_path / "agents" / "spending-agent"
    SessionStore(main_dir, "term-main1").load()          # main's thread
    SessionStore(sp_dir, "term-sp1").load()              # spending-agent's threads
    SessionStore(sp_dir, "term-sp2").load()

    class _Reg:
        _dirs = {"main": main_dir, "spending-agent": sp_dir}

        def get(self, aid):
            if aid not in self._dirs:
                raise KeyError(aid)
            return SimpleNamespace(state_dir=self._dirs[aid])

    gw = Gateway(config=SimpleNamespace(state_dir=main_dir), service=None, registry=_Reg())

    default = gw._sessions_list({})                       # no agentId -> default (main)
    assert {s["sessionId"] for s in default["sessions"]} == {"term-main1"}
    assert default["agentId"] == "main"

    scoped = gw._sessions_list({"agentId": "spending-agent"})   # the agent's OWN threads
    assert {s["sessionId"] for s in scoped["sessions"]} == {"term-sp1", "term-sp2"}
    assert scoped["agentId"] == "spending-agent"

    unknown = gw._sessions_list({"agentId": "nope"})     # unknown -> falls back to default
    assert unknown["agentId"] == "main"
