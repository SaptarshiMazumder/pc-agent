"""L1 — LineChannel: signature verify, inbound normalization + policy, and
reply-token-first/push outbound. Pure logic, no network (httpx POST is injected)."""

import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentd.infrastructure.channels.line_channel import LineChannel

SECRET = "shh-secret"


def _sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


def _event_body(text="hi", user="U123", msg_id="m1", reply_token="rt", source=None) -> bytes:
    src = source or {"type": "user", "userId": user}
    return json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": reply_token,
                    "timestamp": 1700000000000,
                    "source": src,
                    "message": {"type": "text", "id": msg_id, "text": text},
                }
            ]
        }
    ).encode()


def _chan(**kw):
    posts: list[tuple[str, dict]] = []

    async def http_post(url, headers, json_body):
        posts.append((url, json_body))
        return 200, "{}"

    kw.setdefault("channel_secret", SECRET)
    kw.setdefault("access_token", "tok")
    return LineChannel(http_post=http_post, **kw), posts


# ---- verify -----------------------------------------------------------------


def test_verify_accepts_valid_and_rejects_tampered():
    ch, _ = _chan()
    body = _event_body()
    assert ch.verify(body, _sign(body)) is True
    assert ch.verify(body, "deadbeef") is False
    assert ch.verify(body + b"x", _sign(body)) is False  # body tampered
    assert ch.verify(body, None) is False  # missing header


# ---- inbound normalization + policy -----------------------------------------


def test_parse_events_normalizes_text_dm():
    ch, _ = _chan()
    (m,) = ch.parse_events(_event_body(text="table for 2", user="Uabc", msg_id="42"))
    assert m.channel == "line" and m.peer == "Uabc" and m.text == "table for 2"
    assert m.external_id == "42" and m.ts == 1700000000.0


def test_parse_events_group_uses_group_id_as_peer():
    ch, _ = _chan()
    body = _event_body(source={"type": "group", "groupId": "Cgrp", "userId": "Ume"})
    (m,) = ch.parse_events(body)
    assert m.peer == "Cgrp"  # reply target = the group


def test_parse_events_skips_non_text_and_bad_json():
    ch, _ = _chan()
    sticker = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "source": {"userId": "U1"},
                    "message": {"type": "sticker", "id": "s1"},
                }
            ]
        }
    ).encode()
    assert ch.parse_events(sticker) == []
    assert ch.parse_events(b"not json at all") == []  # never crashes ingress


def test_policy_allowlist_drops_strangers_open_allows_all():
    allow, _ = _chan(policy="allowlist", allow_from=["Uowner"])
    assert allow.parse_events(_event_body(user="Ustranger")) == []
    (m,) = allow.parse_events(_event_body(user="Uowner"))
    assert m.peer == "Uowner"
    openc, _ = _chan(policy="open")
    assert len(openc.parse_events(_event_body(user="Uanyone"))) == 1
    star, _ = _chan(policy="allowlist", allow_from=["*"])  # "*" = allow all (test/bootstrap)
    assert len(star.parse_events(_event_body(user="Uwhoever"))) == 1


# ---- outbound ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_is_empty_push_channel():
    ch, _ = _chan()
    assert await ch.poll() == []


@pytest.mark.asyncio
async def test_send_uses_reply_token_first_then_push():
    ch, posts = _chan(reply_token_ttl=60.0)
    ch.parse_events(_event_body(user="U1", reply_token="RT1"))  # stashes RT1 for peer U1
    await ch.send("U1", "hello")
    assert posts[0][0].endswith("/message/reply")
    assert posts[0][1]["replyToken"] == "RT1"
    assert posts[0][1]["messages"] == [{"type": "text", "text": "hello"}]
    await ch.send("U1", "again")  # token consumed -> push
    assert posts[1][0].endswith("/message/push") and posts[1][1]["to"] == "U1"


@pytest.mark.asyncio
async def test_send_pushes_when_no_token():
    ch, posts = _chan()
    await ch.send("U9", "hi there")
    assert posts == [
        (
            "https://api.line.me/v2/bot/message/push",
            {"to": "U9", "messages": [{"type": "text", "text": "hi there"}]},
        )
    ]


@pytest.mark.asyncio
async def test_send_expired_token_falls_back_to_push():
    ch, posts = _chan(reply_token_ttl=0.0)  # any age is stale
    ch.parse_events(_event_body(user="U1", reply_token="RT"))
    await ch.send("U1", "hi")
    assert posts[0][0].endswith("/message/push")


@pytest.mark.asyncio
async def test_send_chunks_long_text_over_5000():
    ch, posts = _chan()
    await ch.send("U1", "x" * 12000)  # 5000 + 5000 + 2000, one push call
    assert len(posts) == 1
    assert [len(m["text"]) for m in posts[0][1]["messages"]] == [5000, 5000, 2000]


@pytest.mark.asyncio
async def test_send_empty_text_is_noop():
    ch, posts = _chan()
    await ch.send("U1", "   ")
    assert posts == []
