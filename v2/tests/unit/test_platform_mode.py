"""Local vs Cloud — one machine-wide fact, owned by the daemon, defaulting to Cloud.

THE BUG THIS CLOSES. The mode preference lived in the agentd desktop app's own `localStorage`
(clients/ui/src/lib/mode.ts). An agent's window is a different page with a different store, so it
could neither read the choice nor change it — switching modes meant leaving the agent, opening
agentd, and switching there. Worse, the desktop client re-asserted its copy on every reconnect,
so a mode changed anywhere else was silently undone.

THE TWO REQUIREMENTS FIGHT EACH OTHER, which is why the rule is a function and not a boolean:

  * default Cloud — signing in should land you on platform keys with nothing pressed
  * an explicit Local must STICK — or "default cloud" really means "you can never stay local"

Resolved by distinguishing "no preference" from "chose local". Everything below is that
distinction, tested from both directions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.application.services.platform_mode_service import PlatformModeService
from agent_runtime.domain import platform_mode
from agent_runtime.domain.account_session import ANONYMOUS, AccountSession
from agent_runtime.infrastructure.env_file_platform_mode_store import (
    MODE_KEY,
    EnvFilePlatformModeStore,
)

SIGNED_IN = AccountSession(token="sess_1", email="a@b.c", account_id="acct_1")


# ── the pure rule ───────────────────────────────────────────────────────────
def test_no_preference_means_cloud():
    """The default, and the whole point of the change."""
    assert platform_mode.resolve(platform_mode.UNSET, True, True) == platform_mode.CLOUD


def test_choosing_local_beats_the_default():
    assert platform_mode.resolve(platform_mode.LOCAL, True, True) == platform_mode.LOCAL


@pytest.mark.parametrize(
    "proxy,signed_in", [(False, True), (True, False), (False, False)]
)
def test_cloud_needs_both_a_proxy_and_a_sign_in(proxy, signed_in):
    """Reporting cloud without both halves would promise metered platform keys while the calls
    quietly went out on the user's own."""
    assert platform_mode.resolve(platform_mode.UNSET, proxy, signed_in) == platform_mode.LOCAL


def test_a_junk_preference_falls_back_to_the_default():
    """A daemon that refuses to start because a settings file says `mode = clod` is worse than
    one that treats it as 'not chosen'."""
    assert platform_mode.normalize("clod") == platform_mode.UNSET
    assert platform_mode.resolve("clod", True, True) == platform_mode.CLOUD


# ── doubles ─────────────────────────────────────────────────────────────────
class _Modes:
    def __init__(self, value=platform_mode.UNSET):
        self.value = value

    def read(self):
        return self.value

    def write(self, mode):
        self.value = mode


class _Tokens:
    def __init__(self, session=SIGNED_IN):
        self.session = session

    def read(self):
        return self.session


class _Proxy:
    def __init__(self, available=True, bound=False):
        self.available = available
        self.bound = bound
        self.calls = []

    def bind(self, token):
        self.calls.append(("bind", token))
        self.bound = True

    def unbind(self):
        self.calls.append(("unbind", None))
        self.bound = False


def _service(pref=platform_mode.UNSET, session=SIGNED_IN, available=True, bound=False):
    modes, tokens, proxy = _Modes(pref), _Tokens(session), _Proxy(available, bound)
    return PlatformModeService(modes, tokens, proxy), modes, proxy


# ── applying it ─────────────────────────────────────────────────────────────
def test_apply_binds_the_proxy_on_a_fresh_signed_in_install():
    service, _, proxy = _service()
    assert service.apply() == platform_mode.CLOUD
    assert proxy.calls == [("bind", "sess_1")]


def test_apply_leaves_an_explicit_local_alone():
    service, _, proxy = _service(pref=platform_mode.LOCAL, bound=True)
    assert service.apply() == platform_mode.LOCAL
    assert proxy.calls == [("unbind", None)]


def test_apply_is_idempotent():
    """It runs at boot AND after every sign-in. A second call must not thrash the proxy."""
    service, _, proxy = _service(bound=True)
    assert service.apply() == platform_mode.CLOUD
    assert proxy.calls == [], "already bound — nothing to do"


def test_signing_out_drops_cloud():
    """There is no such thing as signed out and still metering somebody's account."""
    service, _, proxy = _service(session=ANONYMOUS, bound=True)
    assert service.apply() == platform_mode.LOCAL
    assert proxy.calls == [("unbind", None)]


def test_a_build_with_no_proxy_stays_local_however_it_is_configured():
    service, _, proxy = _service(pref=platform_mode.CLOUD, available=False)
    assert service.apply() == platform_mode.LOCAL


# ── choosing ────────────────────────────────────────────────────────────────
def test_choosing_local_is_written_down():
    """THE LOAD-BEARING ONE. Without the record, the next boot and the next sign-in re-derive
    Cloud from the default and put the user straight back where they did not want to be."""
    service, modes, _ = _service(bound=True)
    assert service.choose("local") == platform_mode.LOCAL
    assert modes.value == platform_mode.LOCAL
    assert service.mode() == platform_mode.LOCAL, "and it survives a re-read"


def test_a_recorded_local_survives_a_later_sign_in():
    service, modes, proxy = _service(pref=platform_mode.LOCAL)
    service.apply()  # what auth.login triggers
    assert service.mode() == platform_mode.LOCAL
    assert not proxy.bound


def test_choosing_cloud_without_a_sign_in_raises():
    """Rather than silently landing in Local. The user pressed a button; a toggle that springs
    back with no message is the failure mode this avoids."""
    service, _, _ = _service(session=ANONYMOUS)
    with pytest.raises(ValueError, match="sign in first"):
        service.choose("cloud")


def test_choosing_cloud_on_a_build_with_no_proxy_raises():
    service, _, _ = _service(available=False)
    with pytest.raises(ValueError, match="no model proxy"):
        service.choose("cloud")


def test_an_unknown_mode_raises():
    service, _, _ = _service()
    with pytest.raises(ValueError, match="unknown mode"):
        service.choose("banana")


def test_remember_records_without_re_applying():
    """platform.connect/disconnect bind and unbind themselves; what they were missing is the
    memory of the choice. Re-applying here would be a second, redundant reconfigure."""
    service, modes, proxy = _service(bound=True)
    service.remember(platform_mode.LOCAL)
    assert modes.value == platform_mode.LOCAL
    assert proxy.calls == []


def test_preference_is_reported_separately_from_the_mode():
    """So a UI can say "Cloud (default)" differently from "Cloud (your choice)"."""
    service, _, _ = _service()
    assert service.mode() == platform_mode.CLOUD
    assert service.preference() == platform_mode.UNSET


def test_can_use_cloud_is_both_halves():
    assert _service()[0].can_use_cloud is True
    assert _service(session=ANONYMOUS)[0].can_use_cloud is False
    assert _service(available=False)[0].can_use_cloud is False


# ── the store ───────────────────────────────────────────────────────────────
def test_the_preference_is_kept_in_the_env_file_not_the_tracked_config(tmp_path, monkeypatch):
    """agentd.config.json is tracked in git; the .env is not. Pressing a button in a settings
    screen must not produce a diff in the repository."""
    monkeypatch.delenv(MODE_KEY, raising=False)
    env = tmp_path / ".env"
    store = EnvFilePlatformModeStore(env)
    assert store.read() == platform_mode.UNSET

    store.write(platform_mode.LOCAL)
    assert f"{MODE_KEY}=local" in env.read_text(encoding="utf-8")
    assert store.read() == platform_mode.LOCAL


def test_the_store_never_returns_junk(tmp_path, monkeypatch):
    monkeypatch.setenv(MODE_KEY, "sideways")
    assert EnvFilePlatformModeStore(tmp_path / ".env").read() == platform_mode.UNSET


# ── the wire ────────────────────────────────────────────────────────────────
def _gateway(service):
    from agent_runtime.presentation.gateway import Gateway

    class _Cfg:
        accounts = {}
        distribution = None
        state_dir = "."

    return Gateway(config=_Cfg(), service=None, platform_mode=service)


def test_status_reports_the_mode_the_preference_and_whether_cloud_is_reachable():
    """One machine-wide fact with one machine-wide home: every UI reads these three, so an agent
    page and the agentd window can no longer disagree about what mode this daemon is in."""
    service, _, _ = _service()
    status = _gateway(service)._platform_status()
    assert status["mode"] == platform_mode.CLOUD
    assert status["modePreference"] == platform_mode.UNSET
    assert status["canUseCloud"] is True


def test_connect_needs_no_token_from_the_caller(tmp_path, monkeypatch):
    """The switch lives in an agent's settings page, and that page has no credential — by design,
    since the daemon now performs sign-in and keeps the token. Requiring one to be passed back in
    through a request is a leftover from when identity and billing were the same value."""
    from agent_runtime import runtime_paths
    from agent_runtime.infrastructure import accounts
    from agent_runtime.infrastructure.llm import model_proxy

    env = tmp_path / ".env"
    monkeypatch.setattr(runtime_paths, "user_env_file", lambda: env)
    monkeypatch.delenv("AGENTD_MODEL_PROXY_KEY", raising=False)
    monkeypatch.setattr(accounts, "enabled", lambda: False)
    monkeypatch.setattr(model_proxy, "configure", lambda _cfg: None)

    service, modes, _ = _service()
    gateway = _gateway(service)
    gateway._platform_connect({})  # no token — the daemon supplies its own

    assert "AGENTD_MODEL_PROXY_KEY=sess_1" in env.read_text(encoding="utf-8")
    assert modes.value == platform_mode.CLOUD, "and the choice is remembered, not just applied"


def test_connect_with_nothing_stored_says_to_sign_in(tmp_path, monkeypatch):
    from agent_runtime import runtime_paths
    from agent_runtime.infrastructure import accounts

    monkeypatch.setattr(runtime_paths, "user_env_file", lambda: tmp_path / ".env")
    monkeypatch.setattr(accounts, "enabled", lambda: False)

    service, _, _ = _service(session=ANONYMOUS)
    with pytest.raises(ValueError, match="sign in first"):
        _gateway(service)._platform_connect({})


def test_signing_out_over_the_wire_also_stops_the_billing():
    """"Signed out but still metering your account" must not be reachable.

    It WAS. The desktop client's Sign out only cleared its own stored session, so the daemon kept
    both the identity token and the proxy credential: the window said signed out while the account
    was still being billed, and the run-mode switch would happily turn Cloud back on because the
    daemon still had everything it needed. auth.logout re-applies the mode, so the credential goes
    with the identity."""
    from agent_runtime.application.services.sign_in_service import SignInService

    class _Tokens2:
        def __init__(self):
            self.session = SIGNED_IN

        def read(self):
            return self.session

        def write(self, session):
            self.session = session

        def clear(self):
            self.session = ANONYMOUS

    tokens = _Tokens2()
    proxy = _Proxy(available=True, bound=True)
    mode = PlatformModeService(_Modes(), tokens, proxy)
    gateway = _gateway(mode)
    gateway.sign_in = SignInService(accounts=None, tokens=tokens)

    assert mode.mode() == platform_mode.CLOUD
    gateway._auth_logout()

    assert not tokens.read().signed_in
    assert proxy.calls == [("unbind", None)], "the billing credential goes with the identity"
    assert mode.mode() == platform_mode.LOCAL


def test_a_scoped_agent_window_is_allowed_to_hear_auth_changed():
    """Identity and run mode are machine-wide, so a change made in ANY window is a change in all
    of them. This is the delivery half of the fix: the daemon knows, and every connection has to
    be told — a window still showing an account the daemon has forgotten is not a rendering
    glitch, it is a client acting as somebody who is no longer signed in."""
    from agent_runtime.presentation.gateway import _scoped_event_allowed

    assert _scoped_event_allowed("auth.changed", {}, "any-agent") is True


def test_the_token_is_host_only():
    """`auth.token` hands back the real credential, so it must never be reachable from an agent's
    page — that code may have been downloaded from a marketplace and is served with no CSP. The
    boundary is the existing scope gate: a scoped connection is refused anything outside
    APP_SCOPED_METHODS, so keeping it OUT of that list is the whole enforcement."""
    from agent_runtime.presentation.gateway import APP_SCOPED_METHODS

    assert "auth.token" not in APP_SCOPED_METHODS
    assert {"auth.status", "auth.login", "auth.logout"} <= set(APP_SCOPED_METHODS), (
        "the tokenless three stay available to agent pages"
    )


def test_the_broadcast_payload_carries_no_token():
    """Same rule as every other auth surface: a page learns WHETHER someone is signed in, never
    the credential. This one goes to every connection at once, including scoped agent windows
    whose code may have been downloaded from a marketplace."""
    service, _, _ = _service()
    state = _gateway(service)._auth_state()
    assert set(state) == {"available", "signedIn", "email", "accountId", "mode", "canUseCloud"}
    assert "sess_1" not in str(state)


def test_status_still_answers_on_a_daemon_with_no_mode_service():
    from agent_runtime.presentation.gateway import Gateway

    class _Cfg:
        accounts = {}
        distribution = None
        state_dir = "."

    status = Gateway(config=_Cfg(), service=None)._platform_status()
    assert status["mode"] == platform_mode.LOCAL
    assert status["canUseCloud"] is False
