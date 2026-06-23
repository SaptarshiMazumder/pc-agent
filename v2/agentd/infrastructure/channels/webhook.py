"""WebhookServer — HTTP ingress for PUSH channels (L2).

LINE (and Slack/Telegram-webhook) deliver events as signed HTTP POSTs rather than by
polling. This one server hosts every push channel on its own ``webhook_path`` (so the
restaurant OA and an owner channel coexist on different paths), and for each event:

  1. verify the signature (the channel knows its HMAC + which header carries it),
  2. **ack 200 immediately** (platforms retry on slow/!2xx responses),
  3. dedup on the platform event id,
  4. fire each message through the gateway's normal channel path (``_fire_channel``).

Mirrors ChannelPoller: a generic driver constructed with ``(channels, fire)``, started in
``serve()``, fault-isolated. Transport-agnostic — it only needs ``webhook_path``,
``verify``, ``parse_events`` (+ optional ``signature_header``) from a channel.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

log = logging.getLogger(__name__)


class WebhookServer:
    def __init__(self, channels, fire, *, host: str = "0.0.0.0", port: int = 8788,
                 seen: set | None = None, max_seen: int = 10000):
        self._channels = {c.webhook_path: c for c in channels}   # path -> channel
        self._fire = fire                       # async (channel, InboundMessage) -> None
        self._host, self._port = host, port
        self._seen = seen if seen is not None else set()         # processed event ids (dedup)
        self._max_seen = max_seen
        self._inflight: set[asyncio.Task] = set()
        self._runner: web.AppRunner | None = None

    async def _handle(self, request: web.Request) -> web.Response:
        ch = self._channels.get(request.path)
        if ch is None:
            return web.Response(status=404)
        body = await request.read()
        sig = request.headers.get(getattr(ch, "signature_header", "X-Line-Signature"), "")
        if not ch.verify(body, sig):
            return web.Response(status=401, text="bad signature")
        try:
            for msg in ch.parse_events(body):
                eid = msg.external_id
                if eid and eid in self._seen:
                    continue                    # redelivered webhook -> skip
                if eid:
                    self._remember(eid)
                task = asyncio.create_task(self._fire(ch, msg))
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)
        except Exception:  # noqa: BLE001 — never 500 (would trigger platform retries); ack + move on
            log.warning("webhook dispatch error on %s", request.path, exc_info=True)
        return web.Response(text="OK")          # ack now; the run executes async

    async def _verify_endpoint(self, request: web.Request) -> web.Response:
        return web.Response(text="OK")          # LINE console "Verify" button does a GET

    def _remember(self, eid: str) -> None:
        if len(self._seen) >= self._max_seen:   # crude cap; durable dedup is L5
            self._seen.clear()
        self._seen.add(eid)

    async def run(self) -> None:
        app = web.Application()
        for path in self._channels:
            app.router.add_post(path, self._handle)
            app.router.add_get(path, self._verify_endpoint)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._host, self._port).start()
        log.info("webhook server on http://%s:%s paths=%s",
                 self._host, self._port, list(self._channels))
        try:
            await asyncio.Future()              # serve until cancelled
        finally:
            await self._runner.cleanup()
