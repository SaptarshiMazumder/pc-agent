"""L2 — WebhookServer: signature gate (401), ack-200, dedup, fan-out to fire(),
and path-based routing across multiple channels. Drives _handle directly with a fake
request (no live socket), plus one end-to-end signed POST through a real LineChannel."""

import asyncio
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentd.domain.channels import InboundMessage
from agentd.infrastructure.channels.line_channel import LineChannel
from agentd.infrastructure.channels.webhook import WebhookServer


class _Req:
    def __init__(self, path, headers, body=b""):
        self.path, self.headers, self._body = path, headers, body

    async def read(self):
        return self._body


class _FakeChannel:
    name = "line"
    signature_header = "X-Sig"

    def __init__(self, path="/line/webhook", ok=True, msgs=None):
        self.webhook_path = path
        self._ok, self._msgs = ok, msgs or []

    def verify(self, body, sig):
        return self._ok and sig == "good"

    def parse_events(self, body):
        return list(self._msgs)


def _server(channels):
    fired = []

    async def fire(ch, msg):
        fired.append((ch.name, msg.peer, msg.text, msg.external_id))

    return WebhookServer(channels, fire), fired


async def _drain(srv):
    if srv._inflight:
        await asyncio.gather(*list(srv._inflight))


@pytest.mark.asyncio
async def test_valid_signed_post_acks_200_and_fires():
    ch = _FakeChannel(msgs=[InboundMessage(channel="line", peer="U1", text="hi", external_id="m1")])
    srv, fired = _server([ch])
    resp = await srv._handle(_Req("/line/webhook", {"X-Sig": "good"}, b"{}"))
    await _drain(srv)
    assert resp.status == 200
    assert fired == [("line", "U1", "hi", "m1")]


@pytest.mark.asyncio
async def test_bad_signature_returns_401_and_does_not_fire():
    ch = _FakeChannel(msgs=[InboundMessage(channel="line", peer="U1", text="hi", external_id="m1")])
    srv, fired = _server([ch])
    resp = await srv._handle(_Req("/line/webhook", {"X-Sig": "WRONG"}, b"{}"))
    await _drain(srv)
    assert resp.status == 401 and fired == []


@pytest.mark.asyncio
async def test_unknown_path_404():
    srv, _ = _server([_FakeChannel()])
    resp = await srv._handle(_Req("/nope", {"X-Sig": "good"}, b"{}"))
    assert resp.status == 404


@pytest.mark.asyncio
async def test_duplicate_event_id_fires_once():
    msg = InboundMessage(channel="line", peer="U1", text="hi", external_id="dup")
    ch = _FakeChannel(msgs=[msg])
    srv, fired = _server([ch])
    await srv._handle(_Req("/line/webhook", {"X-Sig": "good"}, b"{}"))
    await srv._handle(_Req("/line/webhook", {"X-Sig": "good"}, b"{}"))  # redelivery
    await _drain(srv)
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_two_channels_route_independently_by_path():
    a = _FakeChannel(
        path="/line/webhook",
        msgs=[InboundMessage(channel="line", peer="cust", text="a", external_id="1")],
    )
    b = _FakeChannel(
        path="/line-owner/webhook",
        msgs=[InboundMessage(channel="line", peer="owner", text="b", external_id="2")],
    )
    srv, fired = _server([a, b])
    await srv._handle(_Req("/line-owner/webhook", {"X-Sig": "good"}, b"{}"))
    await _drain(srv)
    assert fired == [("line", "owner", "b", "2")]  # only channel b's path


@pytest.mark.asyncio
async def test_get_verify_endpoint_returns_200():
    srv, _ = _server([_FakeChannel()])
    resp = await srv._verify_endpoint(_Req("/line/webhook", {}))
    assert resp.status == 200


@pytest.mark.asyncio
async def test_end_to_end_real_linechannel_signed_post():
    secret = "topsecret"
    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "replyToken": "rt",
                    "timestamp": 1700000000000,
                    "source": {"type": "user", "userId": "Uxyz"},
                    "message": {"type": "text", "id": "m99", "text": "table for 4"},
                }
            ]
        }
    ).encode()
    sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()

    ch = LineChannel(channel_secret=secret, access_token="tok", agent_id="restaurant")
    srv, fired = _server([ch])
    resp = await srv._handle(_Req("/line/webhook", {"X-Line-Signature": sig}, body))
    await _drain(srv)
    assert resp.status == 200
    assert fired == [("line", "Uxyz", "table for 4", "m99")]
    # tampered body under the same sig is rejected
    bad = await srv._handle(_Req("/line/webhook", {"X-Line-Signature": sig}, body + b" "))
    assert bad.status == 401
