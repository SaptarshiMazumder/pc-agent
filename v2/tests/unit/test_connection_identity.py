"""Identity and billing belong to the CONNECTION. The daemon stores neither.

WHAT THIS REPLACED, and why it had to go. The daemon kept the signed-in session in one file and
the billing credential in another — one of each, for the whole machine. That is defensible for one
person on one laptop and indefensible for anything else:

  * the second person to sign in overwrote the first
  * signing out signed out everybody
  * a window nobody had signed in on spent whoever had last pressed Cloud, and the daemon could
    not even attribute the spend, because usage is metered from the turn's account and that
    connection had none

Both facts now arrive on the socket — `?session=` and `?mode=` — and are pinned to it. A hundred
users on one daemon is a hundred connections, each answering for itself, with no shared slot.

The client owns both, because the client is the only thing that can: it is per user by
construction, and the daemon is not.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.infrastructure import accounts
from agent_runtime.infrastructure.llm import model_proxy
from agent_runtime.presentation.gateway import APP_SCOPED_METHODS, Gateway


class _Request:
    def __init__(self, query: str):
        self.path = f"/?{query}"
        self.headers = {}


class _Ws:
    def __init__(self, query: str = ""):
        self.request = _Request(query)


def _cfg(**kw):
    class _C:
        accounts = {}
        distribution = None
        state_dir = "."
        model_proxy = {}
        model_gateway = {}

    c = _C()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _gateway():
    return Gateway(config=_cfg(), service=None)


# ── what the connection carries ─────────────────────────────────────────────
def test_the_session_is_read_from_its_own_parameter():
    assert _gateway()._presented_session(_Ws("token=machine&session=sess_a")) == "sess_a"


def test_it_falls_back_to_token_for_a_hosted_client():
    """A hosted client has always sent its session as `?token=` — it is the only credential there.
    The fallback is what lets one server-side rule serve both without changing that client."""
    assert _gateway()._presented_session(_Ws("token=sess_a")) == "sess_a"


def test_the_machine_token_and_the_session_are_not_confused():
    """One is a machine secret that says you MAY connect; the other identifies a person. A laptop
    sends both, and reading the wrong one would either refuse the user or misidentify them."""
    gateway, ws = _gateway(), _Ws("token=machine-secret&session=sess_a")
    assert gateway._presented_session(ws) == "sess_a"
    assert gateway._presented_token(ws) == "machine-secret"


@pytest.mark.parametrize("query,expected", [
    ("mode=local", "local"), ("mode=cloud", "cloud"), ("mode=CLOUD", "cloud"), ("", ""),
])
def test_the_mode_is_read_from_the_connection(query, expected):
    assert _gateway()._presented_mode(_Ws(query)) == expected


# ── who pays, per turn ──────────────────────────────────────────────────────
@pytest.fixture
def cloud_build(monkeypatch):
    """A build with a proxy URL and no operator key — the desktop case."""
    for name in ("AGENTD_MODEL_PROXY_KEY", "AGENTD_MODEL_PROXY_URL",
                 "AGENTD_MODEL_GATEWAY_KEY", "AGENTD_MODEL_GATEWAY_URL"):
        monkeypatch.delenv(name, raising=False)
    model_proxy.configure(_cfg(model_proxy={"api_base": "http://proxy:4000"}))
    yield
    model_proxy.configure(_cfg())


def _on(account, mode=""):
    return accounts.set_account(account), model_proxy.set_billing(mode)


def _off(tokens):
    accounts.reset_account(tokens[0])
    model_proxy.reset_billing(tokens[1])


def test_a_signed_in_connection_pays_with_its_own_session(cloud_build):
    t = _on({"account_id": "a1", "session_token": "sess_a"})
    try:
        kwargs = model_proxy.apply({"model": "gpt-5"})
        assert kwargs["api_key"] == "sess_a"
        assert kwargs["model"] == "litellm_proxy/gpt-5"
    finally:
        _off(t)


def test_an_unsigned_connection_stays_on_its_own_keys(cloud_build):
    """THE BUG THIS CLOSES. It used to be proxied anyway, and billed to whoever had pressed Cloud
    in some other window."""
    t = _on(None)
    try:
        assert model_proxy.apply({"model": "gpt-5"}) == {"model": "gpt-5"}
    finally:
        _off(t)


def test_two_connections_are_billed_to_two_different_accounts(cloud_build):
    seen = []
    for who in ("sess_a", "sess_b"):
        t = _on({"account_id": who, "session_token": who})
        try:
            seen.append(model_proxy.apply({"model": "gpt-5"})["api_key"])
        finally:
            _off(t)
    assert seen == ["sess_a", "sess_b"], "same daemon, same moment, different payers"


def test_choosing_local_keeps_you_signed_in(cloud_build):
    """"Use my own API keys" must not mean "log me out" — publishing and agent ownership still
    need to know who you are while the calls go out on your own keys."""
    t = _on({"account_id": "a1", "session_token": "sess_a"}, mode="local")
    try:
        assert model_proxy.apply({"model": "gpt-5"}) == {"model": "gpt-5"}
        assert accounts.account_id() == "a1"
    finally:
        _off(t)


def test_an_operator_forced_proxy_routes_everything(monkeypatch):
    """A hosted deployment's rule is the operator's: with the proxy forced on, calls route even
    from a connection with nothing of its own to pay with."""
    monkeypatch.setenv("AGENTD_MODEL_PROXY_URL", "http://proxy:4000")
    monkeypatch.delenv("AGENTD_MODEL_PROXY_KEY", raising=False)
    model_proxy.configure(_cfg())
    t = _on(None)
    try:
        assert model_proxy.apply({"model": "gpt-5"})["model"] == "litellm_proxy/gpt-5"
    finally:
        _off(t)
        model_proxy.configure(_cfg())


def test_a_stale_key_in_the_env_cannot_decide_who_is_billed(monkeypatch):
    """NO FALLBACK CHAIN. Reading `session or _api_key` meant a connection nobody had signed in on
    quietly spent whatever key was left in the machine's .env — the very thing this removes."""
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "left-over-from-an-old-sign-in")
    monkeypatch.delenv("AGENTD_MODEL_PROXY_URL", raising=False)
    model_proxy.configure(_cfg(model_proxy={"api_base": "http://proxy:4000"}))
    t = _on(None)
    try:
        assert model_proxy.apply({"model": "gpt-5"}) == {"model": "gpt-5"}, "ignored, not spent"
        assert model_proxy.enabled() is False
    finally:
        _off(t)
        model_proxy.configure(_cfg())


# ── nothing is stored, and nothing is asked of the daemon ───────────────────
def test_the_daemon_offers_no_sign_in_methods():
    """Sign-in is plain HTTP from the client to the accounts service. Routing it through the
    daemon is what created a machine-wide session in the first place."""
    assert not any(m.startswith("auth.") for m in APP_SCOPED_METHODS)
    for gone in ("_auth_login", "_auth_status", "_auth_logout", "_auth_token"):
        assert not hasattr(Gateway, gone), f"{gone} should not exist"


def test_status_reports_this_connection_not_the_machine(cloud_build):
    # IDENTITY is the connection's (signedIn/email track set_account); MODE is the DAEMON's —
    # its persisted run_mode, not a per-connection guess. This gateway has no run_mode set and is
    # not hosted, so mode resolves to LOCAL ("your own keys, no surprise billing" until a user
    # explicitly switches), the SAME answer whether or not someone is signed in — which is exactly
    # the property that replaced reading ?mode= off the socket (two windows could disagree before).
    gateway = _gateway()
    t = _on({"account_id": "a1", "email": "a@b.c", "session_token": "sess_a"})
    try:
        status = gateway._platform_status()
        assert status["signedIn"] is True and status["email"] == "a@b.c"
        assert status["mode"] == "local"  # the daemon's mode, unchanged by who is signed in
        assert status["canUseCloud"] is True, "a Cloud exists to switch to"
    finally:
        _off(t)
    t = _on(None)
    try:
        status = gateway._platform_status()
        assert status["signedIn"] is False
        assert status["mode"] == "local"
        assert status["canUseCloud"] is True, "a Cloud exists to switch to"
    finally:
        _off(t)
