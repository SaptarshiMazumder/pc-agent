"""Connect-link flow: one-time tokens (mint/resolve/consume/expire); the /connect web form
writes the captured login to the vault and consumes the token; simple_login hands the user a
link when no login is saved."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.run_context import RunContext, set_run_context
from agentd.infrastructure.channels.webhook import WebhookServer
from agentd.infrastructure.credentials import ConnectTokenStore
from agentd.infrastructure.tools.login_tool import SimpleLoginTool


def test_token_mint_resolve_consume_is_single_use():
    ts = ConnectTokenStore()
    t = ts.mint("cost-calc", "hotpepper")
    assert ts.resolve(t) == ("cost-calc", "hotpepper")
    assert ts.consume(t) == ("cost-calc", "hotpepper")
    assert ts.resolve(t) is None and ts.consume(t) is None        # consumed -> dead


def test_token_expiry():
    ts = ConnectTokenStore(ttl_seconds=-1)                        # already expired
    assert ts.resolve(ts.mint("a", "s")) is None


def test_link_format():
    assert ConnectTokenStore.link("https://x.ngrok-free.app/", "abc") \
        == "https://x.ngrok-free.app/connect/abc"


class _Req:
    def __init__(self, token, data=None):
        self.match_info = {"token": token}
        self._data = data or {}

    async def post(self):
        return self._data


class _Vault:
    def __init__(self):
        self.saved = []

    def put(self, agent, cred):
        self.saved.append((agent, cred))

    def get(self, agent, site):
        return None


def test_connect_form_renders_and_404s_on_bad_token():
    ts = ConnectTokenStore()
    srv = WebhookServer([], None, credential_store=_Vault(), connect_tokens=ts)
    ok = asyncio.run(srv._connect_form(_Req(ts.mint("cost-calc", "hotpepper"))))
    assert ok.status == 200 and "Connect your hotpepper login" in ok.text
    assert asyncio.run(srv._connect_form(_Req("nope"))).status == 404


def test_connect_submit_writes_vault_and_consumes_token():
    ts, vault = ConnectTokenStore(), _Vault()
    srv = WebhookServer([], None, credential_store=vault, connect_tokens=ts)
    t = ts.mint("cost-calc", "hotpepper")
    resp = asyncio.run(srv._connect_submit(_Req(t, {
        "login_url": "https://salonboard/login", "username": "me@x.com", "password": "PW123"})))
    assert resp.status == 200 and "Connected" in resp.text
    assert len(vault.saved) == 1
    agent, cred = vault.saved[0]
    assert agent == "cost-calc" and cred.site == "hotpepper"
    assert cred.username == "me@x.com" and cred.password == "PW123"
    # the token is single-use -> a replay fails
    again = asyncio.run(srv._connect_submit(_Req(t, {"login_url": "x", "username": "y", "password": "z"})))
    assert again.status == 404


def _login_tool(public_url):
    class S:
        def get(self, agent, site):
            return None
    return SimpleLoginTool(S(), browser_manager=None,
                           connect_tokens=ConnectTokenStore(), public_url=public_url)


def test_simple_login_no_creds_returns_connect_link():
    set_run_context(RunContext("cost-calc", "agent:cost-calc:line:U1", "channel"))
    r = asyncio.run(_login_tool("https://x.ngrok-free.app").execute(
        "c", {"site": "hotpepper"}, asyncio.Event()))
    assert r.is_error and "https://x.ngrok-free.app/connect/" in r.content[0].text


def test_simple_login_no_creds_without_public_url_explains():
    set_run_context(RunContext("main", "agent:main:dev", "interactive"))
    r = asyncio.run(_login_tool("").execute("c", {"site": "hp"}, asyncio.Event()))
    assert r.is_error and "AGENTD_PUBLIC_URL" in r.content[0].text
