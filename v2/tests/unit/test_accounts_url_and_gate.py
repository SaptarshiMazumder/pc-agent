"""The accounts URL: ONE resolution, and knowing it must not lock anyone out.

Two bugs are pinned here, and they are opposite failures of the same missing distinction.

TOO LITTLE. The URL was resolved in two places that looked in different sets of sources: the
accounts seam read env + config and ignored the distribution profile, while the gateway's platform
status read the profile and ignored env + config. Put an accounts URL in `agentd.config.json` and
the daemon configured itself while every client was still told this build had no sign-in — which
is how an agent ships a login screen that renders nothing.

TOO MUCH. `accounts.enabled()` meant BOTH "clients may sign in" and "clients MUST present an
account token or be closed with 4401". So the act of configuring sign-in was also the act of
locking the operator out of their own daemon: the connection gate stops accepting the machine
token the moment accounts are enforced.

Advertised and enforced are now separate. `available()` says a login can be offered; `enabled()`
says one is demanded, and only on an explicit opt-in.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.config import accounts_api_base
from agent_runtime.infrastructure import accounts

SERVICE = "https://accounts.example.com"


class _Profile:
    def __init__(self, accounts_url=""):
        self.accounts_url = accounts_url


class _Config:
    def __init__(self, accounts=None, profile=""):
        self.accounts = accounts or {}
        self.distribution = _Profile(profile)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AGENTD_ACCOUNTS_URL", raising=False)


# ── one resolution, three sources ───────────────────────────────────────────
def test_it_comes_from_the_distribution_profile():
    assert accounts_api_base(_Config(profile=SERVICE)) == SERVICE


def test_it_comes_from_the_config_file():
    """The case that was broken: the client half never looked here, so this configured the server
    and left every UI believing there was nothing to sign in to."""
    assert accounts_api_base(_Config(accounts={"api_base": SERVICE})) == SERVICE


def test_config_beats_the_profile(monkeypatch):
    cfg = _Config(accounts={"api_base": SERVICE}, profile="https://baked-in.example.com")
    assert accounts_api_base(cfg) == SERVICE


def test_the_environment_beats_everything(monkeypatch):
    monkeypatch.setenv("AGENTD_ACCOUNTS_URL", "https://from-env.example.com")
    cfg = _Config(accounts={"api_base": SERVICE}, profile=SERVICE)
    assert accounts_api_base(cfg) == "https://from-env.example.com"


def test_a_trailing_slash_never_survives():
    """Every caller concatenates a path onto this. One slash here becomes `//login` everywhere."""
    assert accounts_api_base(_Config(accounts={"api_base": SERVICE + "/"})) == SERVICE


def test_nothing_configured_is_an_empty_string():
    assert accounts_api_base(_Config()) == ""


# ── advertised is not enforced ──────────────────────────────────────────────
def test_a_url_alone_offers_sign_in_without_demanding_it():
    """THE LOCKOUT REGRESSION. Configuring where people log in must not change how existing
    connections authenticate — otherwise turning auth on closes the operator's own client with
    4401 and the only way back is editing the config file blind."""
    accounts.configure(_Config(accounts={"api_base": SERVICE}))
    assert accounts.available() is True, "a UI can offer a login"
    assert accounts.enabled() is False, "but nothing is required to present an account token"


def test_a_profile_url_alone_also_only_advertises():
    accounts.configure(_Config(profile=SERVICE))
    assert accounts.available() is True
    assert accounts.enabled() is False


def test_enforcement_needs_an_explicit_opt_in():
    accounts.configure(_Config(accounts={"api_base": SERVICE, "enabled": True}))
    assert accounts.enabled() is True


def test_the_env_var_still_enforces(monkeypatch):
    """Unchanged for hosted deployments, which are configured entirely by environment."""
    monkeypatch.setenv("AGENTD_ACCOUNTS_URL", SERVICE)
    accounts.configure(_Config())
    assert accounts.enabled() is True


def test_no_url_means_neither():
    accounts.configure(_Config())
    assert accounts.available() is False
    assert accounts.enabled() is False


def test_configure_reports_the_resolved_url():
    accounts.configure(_Config(profile=SERVICE))
    assert accounts.api_base() == SERVICE


# ── the client half now sees the same answer ────────────────────────────────
def test_platform_status_reports_the_resolved_url_not_the_raw_profile():
    """The two halves can no longer disagree: this is the value a browser reads to decide whether
    a sign-in exists, and it now comes from the same resolver the daemon configures itself with."""
    from agent_runtime.presentation.gateway import Gateway

    cfg = _Config(accounts={"api_base": SERVICE})
    cfg.state_dir = "."
    status = Gateway(config=cfg, service=None)._platform_status()
    assert status["accountsUrl"] == SERVICE


# ── a page with NO machine token can still find out where to sign in ────────
#
# THE DEADLOCK THIS BREAKS. A web-delivered agent's entrance is a marketplace card linking to
# `/apps/<id>/`: no `?token=`, because nobody built that url for the visitor. On a daemon that
# requires sign-in the socket is refused until a session exists, and the form that mints one
# cannot render until it knows where to post. 401 here means the page loops forever showing
# nothing — which is exactly what it did.


def _hosted_gateway(monkeypatch, tmp_path):
    from agent_runtime.presentation.gateway import Gateway

    monkeypatch.setenv("AGENTD_ACCOUNTS_URL", SERVICE)
    cfg = _Config()
    cfg.state_dir = str(tmp_path)
    accounts.configure(cfg)
    gw = Gateway(config=cfg, service=None)
    gw.auth_token = "machine-secret"  # hosted daemons DO set one
    return gw


def _get(gw, path, query=""):
    import json as _json
    from urllib.parse import urlsplit

    response = gw._serve_platform(urlsplit(f"{path}?{query}" if query else path), {})
    return response.status_code, _json.loads(bytes(response.body).decode("utf-8"))


def test_a_tokenless_page_is_told_where_to_sign_in(monkeypatch, tmp_path):
    code, body = _get(_hosted_gateway(monkeypatch, tmp_path), "/platform/status")
    assert code == 200
    assert body["accountsUrl"] == SERVICE
    assert body["signedIn"] is False  # an HTTP request has no socket, so no identity


def test_a_browser_is_given_the_public_url_not_internal_service_dns(monkeypatch, tmp_path):
    """THE BUG THIS PINS, caught live on the dev deployment: the hosted daemon reaches accounts at
    `http://accounts.agentd.local:4100`, so that is what it advertised — and a visitor's browser
    cannot resolve it. The gate rendered, took a password, and had nowhere to send it."""
    from agent_runtime.config import client_accounts_url

    gw = _hosted_gateway(monkeypatch, tmp_path)  # AGENTD_ACCOUNTS_URL = the internal-style value
    gw.config.public_accounts_url = "http://alb.example:4100"

    assert client_accounts_url(gw.config) == "http://alb.example:4100"
    for body in (_get(gw, "/platform/status")[1], gw._platform_status()):
        assert body["accountsUrl"] == "http://alb.example:4100"


def test_without_a_public_url_the_two_are_the_same(monkeypatch, tmp_path):
    """Desktop, BYOK and a local checkout all reach accounts at the same address a browser does,
    so nothing is configured there and the fallback must be exact."""
    from agent_runtime.config import client_accounts_url

    gw = _hosted_gateway(monkeypatch, tmp_path)
    assert client_accounts_url(gw.config) == SERVICE


def test_the_hint_withholds_everything_a_gate_does_not_need(monkeypatch, tmp_path):
    """A reduced answer, not the full status: proxy internals are none of an unauthenticated
    caller's business, and widening this endpoint is how an information leak arrives later."""
    _, body = _get(_hosted_gateway(monkeypatch, tmp_path), "/platform/status")
    assert "modelProxy" not in body
    assert "diagnostics" not in body


def test_connect_stays_shut_without_the_machine_token(monkeypatch, tmp_path):
    """Status answers a QUESTION; connect changes state. Only the question is opened up."""
    code, _ = _get(_hosted_gateway(monkeypatch, tmp_path), "/platform/connect", "session=sess_x")
    assert code == 401


def test_a_local_daemon_still_demands_its_token(monkeypatch, tmp_path):
    """The exemption is for deployments that REQUIRE sign-in. A private daemon has no sign-in to
    point at, so the machine token remains the only way in — unchanged."""
    from agent_runtime.presentation.gateway import Gateway

    cfg = _Config()
    cfg.state_dir = str(tmp_path)
    accounts.configure(cfg)  # no url anywhere => not enforced
    gw = Gateway(config=cfg, service=None)
    gw.auth_token = "machine-secret"
    assert _get(gw, "/platform/status")[0] == 401
    assert _get(gw, "/platform/status", "token=machine-secret")[0] == 200
