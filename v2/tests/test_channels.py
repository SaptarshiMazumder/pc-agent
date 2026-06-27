"""Phase 5b — Channels: inbound poll -> run -> reply, conversation binding, and the
no-duplication notify bridge. The framework is proven end-to-end via MemoryChannel;
EmailChannel's logic is checked with a fake MCP invoke."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentd.domain.agent import RunMode, apply_mode
from agentd.domain.messages import AssistantMessage, TextContent
from agentd.infrastructure.channels import ChannelPoller, MemoryChannel, build_channel
from agentd.infrastructure.channels.email_channel import EmailChannel, parse_search
from agentd.infrastructure.memory.local_store import SessionStore
from agentd.infrastructure.notify import ChannelNotifier


# ---- MemoryChannel (reference transport) ------------------------------------

@pytest.mark.asyncio
async def test_memory_channel_poll_dedup_and_send():
    ch = MemoryChannel(agent_id="support")
    ch.inject("alice", "hi")
    first = await ch.poll()
    assert [m.text for m in first] == ["hi"] and first[0].peer == "alice"
    assert await ch.poll() == []                 # already consumed (cursor advanced)
    ch.inject("bob", "yo")
    assert [m.peer for m in await ch.poll()] == ["bob"]
    await ch.send("alice", "hello back")
    assert ch.sent == [("alice", "hello back")]


def test_build_channel_by_type():
    assert isinstance(build_channel({"type": "memory", "agent": "a"}, None), MemoryChannel)
    assert isinstance(build_channel({"type": "email"}, lambda *a: None), EmailChannel)
    assert build_channel({"type": "carrier-pigeon"}, None) is None


def test_build_channel_line_from_config_creds():
    from agentd.infrastructure.channels.line_channel import LineChannel

    ch = build_channel({"type": "line", "agent": "restaurant", "policy": "open",
                        "channel_secret": "s", "access_token": "t"}, None)
    assert isinstance(ch, LineChannel)
    assert ch.agent_id == "restaurant" and ch.policy == "open" and ch.webhook_path == "/line/webhook"


def test_build_channel_line_env_suffix_for_second_account(monkeypatch):
    from agentd.infrastructure.channels.line_channel import LineChannel

    monkeypatch.setenv("LINE_CHANNEL_SECRET_OWNER", "s2")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN_OWNER", "t2")
    ch = build_channel({"type": "line", "agent": "main", "policy": "allowlist",
                        "allow_from": ["Uo"], "env_suffix": "owner",
                        "webhook_path": "/line-owner/webhook"}, None)
    assert isinstance(ch, LineChannel)
    assert ch.policy == "allowlist" and ch.webhook_path == "/line-owner/webhook"


def test_build_channel_line_missing_creds_raises(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValueError):
        build_channel({"type": "line"}, None)


# ---- ChannelNotifier (the no-duplication bridge) ----------------------------

@pytest.mark.asyncio
async def test_channel_notifier_sends_to_owner_via_same_send():
    from agentd.domain.notify import Notification

    ch = MemoryChannel()
    await ChannelNotifier(ch, "owner@me.com").notify(
        Notification(id="1", agent_id="a", kind="blocked", text="job blocked", detail="needs auth"))
    assert ch.sent == [("owner@me.com", "job blocked\n\nneeds auth")]   # one transport, reused


# ---- EmailChannel (Gmail MCP adapter) ---------------------------------------

def test_parse_search_handles_json_shapes_and_garbage():
    assert parse_search('[{"id": "m1", "from": "a@x", "snippet": "hi"}]')[0]["id"] == "m1"
    assert parse_search('{"messages": [{"messageId": "m2", "sender": "b@x", "body": "yo"}]}')[0]["from"] == "b@x"
    assert parse_search("not json at all") == []          # plain text -> nothing, no crash
    assert parse_search('{"weird": 1}') == []


@pytest.mark.asyncio
async def test_email_channel_send_and_poll_via_fake_invoke():
    calls = []

    async def invoke(tool, params):
        calls.append((tool, params))
        if "search" in tool:
            return '[{"id": "m1", "from": "cust@x.com", "body": "need help"}]'
        return "ok"

    ch = EmailChannel(invoke, agent_id="support",
                      cfg={"send_tool": "google__send", "search_tool": "google__search"})
    await ch.send("cust@x.com", "here you go")
    assert ("google__send", {"to": "cust@x.com", "subject": "Re: your message",
                             "body": "here you go"}) in calls

    msgs = await ch.poll()
    assert len(msgs) == 1 and msgs[0].peer == "cust@x.com" and msgs[0].text == "need help"
    assert await ch.poll() == []                          # m1 already seen (dedupe)


# ---- ChannelPoller ----------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_fires_each_new_message_and_isolates_bad_channel():
    fired = []

    async def fire(ch, msg):
        fired.append((ch.name, msg.peer, msg.text))

    good = MemoryChannel()
    good.inject("a", "one")
    good.inject("b", "two")

    class _Bad:
        name = "bad"
        async def poll(self):
            raise RuntimeError("boom")

    await ChannelPoller([_Bad(), good], fire).tick()       # _Bad raises but is isolated
    assert ("memory", "a", "one") in fired and ("memory", "b", "two") in fired


# ---- run mode ---------------------------------------------------------------

def test_channel_mode_hides_autonomous_only_tools():
    from heartbeat_tool import HeartbeatRespondTool
    from outcome_tool import ReportOutcomeTool

    tools = [HeartbeatRespondTool(), ReportOutcomeTool()]
    assert apply_mode(tools, RunMode.CHANNEL) == []        # neither leaks into a channel reply


# ---- gateway: build + bind + reply ------------------------------------------

def test_gateway_build_channels_and_notifiers(tmp_path):
    from agentd.presentation.gateway import Gateway

    cfg = SimpleNamespace(state_dir=tmp_path, channels=[
        {"type": "memory", "agent": "support"},
        {"type": "email", "agent": "sales", "notify_to": "you@me.com"},
    ])
    gw = Gateway(config=cfg, service=None)
    gw._build_channels()
    assert {c.name for c in gw.channels} == {"memory", "email"}
    assert len(gw.channel_notifiers) == 1                  # only the email one had notify_to


@pytest.mark.asyncio
async def test_gateway_channel_inbound_runs_and_replies(tmp_path):
    from agentd.presentation.gateway import Gateway

    class _FakeService:                                    # the "agent": writes a reply msg
        async def handle_message(self, session_id, text, on_event, abort, mode=None, agent_id=None):
            assert mode == RunMode.CHANNEL and agent_id == "support"
            store = SessionStore(tmp_path, session_id)
            store.load()
            store.append(AssistantMessage(content=[TextContent(text=f"got: {text}")]))

    reg = SimpleNamespace(get=lambda aid: SimpleNamespace(state_dir=tmp_path))
    ch = MemoryChannel(agent_id="support")
    ch.inject("alice@x.com", "hello there")
    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path), service=_FakeService(), registry=reg)

    (msg,) = await ch.poll()
    await gw._fire_channel(ch, msg)
    await gw.runs["agent:support:memory:alice@x.com"].task   # let the bound run finish

    assert ch.sent == [("alice@x.com", "got: hello there")]  # reply went back on the SAME channel
