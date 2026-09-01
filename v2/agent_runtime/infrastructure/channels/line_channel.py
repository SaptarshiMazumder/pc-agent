"""LineChannel — a push (webhook) Channel for the LINE Messaging API (L1).

LINE has no polling: events arrive as signed webhook POSTs, delivered to this channel
by the WebhookServer driver (L2). So ``poll()`` returns nothing; the driver verifies the
signature, then this channel parses + normalizes events and sends replies.

ONE transport, three reuses (Channel port): inbound (``parse_events`` via the driver),
replies and notifications (``send``). Replies go **reply-token-first** (free) and fall
back to the **push API** (counts against LINE's quota) once the token is gone/stale.

Security: ``verify`` (HMAC-SHA256 over the raw body) proves the call is from LINE;
``policy`` gates *who* may drive the agent — ``open`` (answer everyone, e.g. a customer
OA) or ``allowlist`` (only ``allow_from`` userIds, e.g. an owner-control channel).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

import httpx

from agent_runtime.domain.channels import InboundMessage

log = logging.getLogger(__name__)

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
TEXT_LIMIT = 5000  # LINE per-message character cap
MAX_MESSAGES_PER_CALL = 5  # LINE per reply/push call


def _chunks(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)]


class LineChannel:
    name = "line"
    signature_header = "X-Line-Signature"  # header the WebhookServer (L2) reads for verify

    def __init__(
        self,
        *,
        agent_id: str = "main",
        channel_secret: str,
        access_token: str,
        policy: str = "open",
        allow_from=(),
        webhook_path: str = "/line/webhook",
        reply_token_ttl: float = 60.0,
        http_post=None,
    ):
        self.agent_id = agent_id
        self.webhook_path = webhook_path  # how the WebhookServer (L2) routes to us
        self.policy = (policy or "open").lower()
        self._allow = {str(s).strip() for s in (allow_from or ()) if str(s).strip()}
        self._secret = (channel_secret or "").encode()
        self._token = access_token or ""
        self._reply_ttl = float(reply_token_ttl)
        # injected for tests; defaults to a real httpx POST. async (url, headers, json) -> (status, text)
        self._post = http_post or self._default_post
        self._reply_tokens: dict[str, tuple[str, float]] = {}  # peer -> (token, monotonic ts)

    # ---- inbound (called by the WebhookServer driver) -----------------------

    def verify(self, body: bytes, signature: str) -> bool:
        """True iff ``signature`` is LINE's HMAC-SHA256(base64) of the RAW body."""
        mac = base64.b64encode(hmac.new(self._secret, body, hashlib.sha256).digest()).decode()
        return hmac.compare_digest(mac, signature or "")

    def parse_events(self, body: bytes) -> list[InboundMessage]:
        """Normalize a (verified) LINE webhook body into InboundMessages.

        Text messages only for MVP. ``peer`` is the reply target (group/room id in a
        group, else the user id); the allow-list is checked against the *sender* userId.
        Stashes each event's ``replyToken`` so ``send`` can use the free reply API."""
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001 — authentic-but-unparseable never crashes ingress
            return []
        out: list[InboundMessage] = []
        for ev in data.get("events") or []:
            if ev.get("type") != "message":
                continue
            msg = ev.get("message") or {}
            if msg.get("type") != "text":
                continue
            src = ev.get("source") or {}
            sender = str(src.get("userId") or "")
            if self.policy == "allowlist" and "*" not in self._allow and sender not in self._allow:
                log.warning("line: dropped message from non-allowed sender %s", sender)
                continue
            peer = str(src.get("groupId") or src.get("roomId") or sender)
            if not peer:
                continue
            token = ev.get("replyToken")
            if token:
                self._reply_tokens[peer] = (token, time.monotonic())
            out.append(
                InboundMessage(
                    channel=self.name,
                    peer=peer,
                    text=str(msg.get("text") or ""),
                    external_id=str(msg.get("id") or ""),
                    ts=float(ev.get("timestamp") or 0) / 1000.0,
                )
            )
        return out

    async def poll(self) -> list[InboundMessage]:
        return []  # push channel: events arrive via the webhook driver, not by polling

    # ---- outbound (replies AND notifications) -------------------------------

    async def send(self, peer: str, text: str) -> None:
        """Deliver text to ``peer``: reply API for the first batch if a fresh reply token
        exists (free), else push. Chunked to LINE's 5000-char / 5-message limits."""
        if not (text or "").strip():
            return
        chunks = _chunks(text)
        groups = [
            chunks[i : i + MAX_MESSAGES_PER_CALL]
            for i in range(0, len(chunks), MAX_MESSAGES_PER_CALL)
        ]
        token = self._take_fresh_token(peer)
        for i, group in enumerate(groups):
            messages = [{"type": "text", "text": c} for c in group]
            if i == 0 and token:
                await self._deliver(LINE_REPLY_URL, {"replyToken": token, "messages": messages})
            else:
                await self._deliver(LINE_PUSH_URL, {"to": peer, "messages": messages})

    def _take_fresh_token(self, peer: str) -> str | None:
        entry = self._reply_tokens.pop(peer, None)  # single-use: always remove
        if entry and (time.monotonic() - entry[1]) < self._reply_ttl:
            return entry[0]
        return None

    async def _deliver(self, url: str, payload: dict) -> None:
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        try:
            status, body = await self._post(url, headers, payload)
        except Exception:  # noqa: BLE001 — best-effort delivery; never raise into the run
            log.warning("line send failed (%s)", url, exc_info=True)
            return
        if status >= 300:
            log.warning("line send %s -> %s: %s", url, status, body)

    async def _default_post(self, url: str, headers: dict, json_body: dict):
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(url, headers=headers, json=json_body)
            return r.status_code, r.text
