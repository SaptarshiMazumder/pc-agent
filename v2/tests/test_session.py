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
