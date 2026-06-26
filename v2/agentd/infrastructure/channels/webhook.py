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

_STYLE = "font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem"


def _page(title: str, body: str) -> str:
    """A minimal self-contained status page (no external resources)."""
    return (f"<!doctype html><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><body style='{_STYLE}'><h2>{title}</h2><p>{body}</p></body>")


def _form_page(token: str, site: str) -> str:
    """The credential-capture form. POSTs over the (TLS) tunnel to /connect/<token>; the password
    field is type=password + autocomplete=off and is never echoed or logged."""
    return (
        f"<!doctype html><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Connect {site}</title><body style='{_STYLE}'>"
        f"<h2>Connect your {site} login</h2>"
        f"<p style='color:#555'>Your assistant uses this to sign in for you. Sent over a secure "
        f"connection and stored encrypted — it never appears in the chat.</p>"
        f"<form method=post action='/connect/{token}' autocomplete=off "
        f"style='display:flex;flex-direction:column;gap:.8rem'>"
        f"<label>Login page URL<br><input name=login_url required placeholder='https://…/login' "
        f"style='padding:.5rem;width:100%;box-sizing:border-box'></label>"
        f"<label>Username / email<br><input name=username required autocomplete=off "
        f"style='padding:.5rem;width:100%;box-sizing:border-box'></label>"
        f"<label>Password<br><input name=password type=password required autocomplete=new-password "
        f"style='padding:.5rem;width:100%;box-sizing:border-box'></label>"
        f"<button type=submit style='padding:.6rem;font-size:1rem'>Connect securely</button>"
        f"</form></body>")


class WebhookServer:
    def __init__(self, channels, fire, *, host: str = "0.0.0.0", port: int = 8788,
                 seen: set | None = None, max_seen: int = 10000,
                 credential_store=None, connect_tokens=None):
        self._channels = {c.webhook_path: c for c in channels}   # path -> channel
        self._fire = fire                       # async (channel, InboundMessage) -> None
        self._host, self._port = host, port
        self._seen = seen if seen is not None else set()         # processed event ids (dedup)
        self._max_seen = max_seen
        # secure /connect login-setup form: validates one-time tokens, writes creds to the vault
        self._creds = credential_store
        self._tokens = connect_tokens
        self._connect_on = credential_store is not None and connect_tokens is not None
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

    # ---- secure /connect login-setup form (one-time links) ------------------

    async def _connect_form(self, request: web.Request) -> web.Response:
        resolved = self._tokens.resolve(request.match_info.get("token", ""))
        if resolved is None:
            return web.Response(status=404, content_type="text/html", text=_page(
                "Link expired", "This setup link is invalid or expired — ask your assistant for a new one."))
        _agent, site = resolved
        return web.Response(content_type="text/html", text=_form_page(request.match_info["token"], site))

    async def _connect_submit(self, request: web.Request) -> web.Response:
        resolved = self._tokens.consume(request.match_info.get("token", ""))   # single-use
        if resolved is None:
            return web.Response(status=404, content_type="text/html", text=_page(
                "Link expired", "This setup link is invalid or has already been used."))
        agent, site = resolved
        data = await request.post()
        login_url = str(data.get("login_url") or "").strip()
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not (login_url and username and password):
            return web.Response(status=400, content_type="text/html", text=_page(
                "Missing info", "Login URL, username and password are all required — "
                "ask your assistant for a fresh link and try again."))
        from agentd.domain.credential import Credential
        self._creds.put(agent, Credential(site=site, login_url=login_url,
                                          username=username, password=password))
        log.info("connect: saved login '%s' for agent '%s' via web form", site, agent)
        return web.Response(content_type="text/html", text=_page(
            "Connected ✓", f"Your {site} login is saved securely. You can close this page."))

    def _remember(self, eid: str) -> None:
        if len(self._seen) >= self._max_seen:   # crude cap; durable dedup is L5
            self._seen.clear()
        self._seen.add(eid)

    async def run(self) -> None:
        app = web.Application()
        for path in self._channels:
            app.router.add_post(path, self._handle)
            app.router.add_get(path, self._verify_endpoint)
        if self._connect_on:                    # secure login-setup form on one-time links
            app.router.add_get("/connect/{token}", self._connect_form)
            app.router.add_post("/connect/{token}", self._connect_submit)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, self._host, self._port).start()
        log.info("webhook server on http://%s:%s paths=%s",
                 self._host, self._port, list(self._channels))
        try:
            await asyncio.Future()              # serve until cancelled
        finally:
            await self._runner.cleanup()
