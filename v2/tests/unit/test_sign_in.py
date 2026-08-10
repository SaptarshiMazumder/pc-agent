"""Sign-in — identity, told apart from billing.

An agent shipped a login screen that rendered nothing, on every machine, forever. The code was
correct; the gate simply never had a reason to appear. Three separate places had welded "who are
you" to "who is paying for your model calls":

  1. The accounts URL reached clients ONLY from the distribution profile — a property of the
     packaged product — while the server half read only config + env. Two halves of one feature
     looking in disjoint places.
  2. The gate skipped itself whenever the platform's keys were already paying.
  3. A sign-in was reported as FAILED unless the paid model proxy switched on, which on a
     bring-your-own-key install it never does.

So a login was impossible unless the whole build was reconfigured as a hosted product. These
tests pin the split: an install can know who you are without anyone selling you anything.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.application.services.sign_in_service import SignInService
from agent_runtime.domain.account_session import ANONYMOUS, AccountSession
from agent_runtime.infrastructure.env_file import EnvFile
from agent_runtime.infrastructure.env_file_session_token_store import (
    EMAIL_KEY,
    TOKEN_KEY,
    EnvFileSessionTokenStore,
)


# ── doubles ─────────────────────────────────────────────────────────────────
class _Accounts:
    """A stand-in accounts service. `error` makes the next login raise, like a wrong password."""

    def __init__(self, available=True, error=None):
        self.available = available
        self._error = error
        self.calls = []

    async def login(self, email, password, signup=False):
        self.calls.append((email, password, signup))
        if self._error:
            raise self._error
        return AccountSession(token="sess_abc", email=email, account_id="acct_1")


class _Tokens:
    def __init__(self, session=ANONYMOUS):
        self.session = session

    def read(self):
        return self.session

    def write(self, session):
        self.session = session

    def clear(self):
        self.session = ANONYMOUS


def _service(**kw):
    return SignInService(accounts=_Accounts(**kw), tokens=_Tokens())


# ── the use case ────────────────────────────────────────────────────────────
def test_a_successful_login_is_remembered():
    service = _service()
    session = asyncio.run(service.login("Person@Example.com ", "hunter2"))
    assert session.token == "sess_abc"
    assert service.session().signed_in
    assert service.session().email == "person@example.com", "normalised before it is stored"


def test_a_rejected_password_raises_rather_than_reporting_signed_out():
    """The failure mode this prevents: a form that clears itself and shows no message. "Wrong
    password" and "not signed in yet" render identically if both come back as signedIn: false."""
    service = SignInService(accounts=_Accounts(error=RuntimeError("incorrect password")),
                            tokens=_Tokens())
    with pytest.raises(RuntimeError, match="incorrect password"):
        asyncio.run(service.login("a@b.c", "wrong"))
    assert not service.session().signed_in, "and nothing was stored"


@pytest.mark.parametrize("email,password", [("", "pw"), ("a@b.c", "")])
def test_blank_fields_raise(email, password):
    with pytest.raises(ValueError):
        asyncio.run(_service().login(email, password))


def test_signup_is_passed_through():
    service = _service()
    accounts = service._accounts
    asyncio.run(service.login("a@b.c", "pw", signup=True))
    assert accounts.calls == [("a@b.c", "pw", True)]


def test_logout_forgets_the_identity():
    service = _service()
    asyncio.run(service.login("a@b.c", "pw"))
    service.logout()
    assert not service.session().signed_in


def test_available_is_false_when_no_accounts_service_is_configured():
    """The ONE legitimate reason for a UI to hide the sign-in prompt. Every other reason the old
    gate hid itself was about billing."""
    assert _service(available=False).available is False


# ── the store: identity is NOT the payment credential ───────────────────────
def test_the_session_is_stored_under_its_own_key(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_KEY, raising=False)
    env = tmp_path / ".env"
    env.write_text("AGENTD_MODEL_PROXY_KEY=pays-for-models\n", encoding="utf-8")

    EnvFileSessionTokenStore(env).write(AccountSession("sess_1", "a@b.c", "acct_1"))

    written = env.read_text(encoding="utf-8")
    assert f"{TOKEN_KEY}=sess_1" in written
    assert "AGENTD_MODEL_PROXY_KEY=pays-for-models" in written, (
        "signing in must not touch the billing credential — they were one value, and that is "
        "why signing in on your own API keys was impossible"
    )


def test_signing_out_leaves_the_payment_credential_alone(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_KEY, raising=False)
    env = tmp_path / ".env"
    store = EnvFileSessionTokenStore(env)
    store.write(AccountSession("sess_1", "a@b.c"))
    EnvFile(env).update({"AGENTD_MODEL_PROXY_KEY": "still-paying"})

    store.clear()

    assert not store.read().signed_in
    assert "still-paying" in env.read_text(encoding="utf-8")


def test_a_stored_session_survives_a_restart(tmp_path, monkeypatch):
    """The email rides along so a restarted daemon can say WHO is signed in without a round trip."""
    monkeypatch.delenv(TOKEN_KEY, raising=False)
    monkeypatch.delenv(EMAIL_KEY, raising=False)
    env = tmp_path / ".env"
    EnvFileSessionTokenStore(env).write(AccountSession("sess_1", "a@b.c", "acct_1"))

    # a fresh process reads the file into the environment at boot
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            monkeypatch.setenv(name, value)

    restored = EnvFileSessionTokenStore(env).read()
    assert restored == AccountSession("sess_1", "a@b.c", "acct_1")


def test_an_email_with_no_token_does_not_read_as_signed_in(monkeypatch, tmp_path):
    monkeypatch.delenv(TOKEN_KEY, raising=False)
    monkeypatch.setenv(EMAIL_KEY, "a@b.c")
    assert not EnvFileSessionTokenStore(tmp_path / ".env").read().signed_in


def test_a_store_that_cannot_write_raises(tmp_path, monkeypatch):
    """Loud, not best-effort. A silent failure signs the user in for as long as the process lives
    and then logs them out at the next restart with no explanation."""
    monkeypatch.delenv(TOKEN_KEY, raising=False)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        EnvFileSessionTokenStore(blocker / ".env").write(AccountSession("sess_1"))


# ── EnvFile: the behaviour four call sites depend on ────────────────────────
def test_updating_one_key_preserves_every_other_line(tmp_path, monkeypatch):
    monkeypatch.delenv("B", raising=False)
    env = tmp_path / ".env"
    env.write_text("# a comment\nA=1\nB=2\n", encoding="utf-8")
    EnvFile(env).update({"B": "changed"})
    text = env.read_text(encoding="utf-8")
    assert "# a comment" in text and "A=1" in text and "B=changed" in text


def test_an_empty_value_removes_the_key_and_unsets_it(tmp_path, monkeypatch):
    monkeypatch.setenv("GONE", "x")
    env = tmp_path / ".env"
    env.write_text("GONE=x\nKEPT=y\n", encoding="utf-8")
    EnvFile(env).update({"GONE": ""})
    assert "GONE" not in env.read_text(encoding="utf-8")
    assert "KEPT=y" in env.read_text(encoding="utf-8")
    import os

    assert "GONE" not in os.environ


def test_a_write_applies_live_because_litellm_reads_keys_at_call_time(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_KEY", raising=False)
    EnvFile(tmp_path / ".env").update({"LIVE_KEY": "now"})
    import os

    assert os.environ["LIVE_KEY"] == "now"


# ── the wire: what a page is allowed to call, and what it gets back ─────────
def _gateway(**kw):
    from agent_runtime.presentation.gateway import Gateway

    class _Cfg:
        accounts = {}
        distribution = None
        state_dir = "."

    return Gateway(config=_Cfg(), service=None, sign_in=SignInService(**kw))


def test_an_agent_page_may_call_the_auth_methods():
    from agent_runtime.presentation.gateway import APP_SCOPED_METHODS

    assert {"auth.status", "auth.login", "auth.logout"} <= set(APP_SCOPED_METHODS)


def test_a_tokenless_visitor_may_not():
    """Absent from the public tier on purpose: a hosted agent page that anyone can open must not
    become a way to drive password attempts through somebody else's daemon."""
    from agent_runtime.presentation.gateway import PUBLIC_APP_METHODS

    assert not ({"auth.login", "auth.logout"} & set(PUBLIC_APP_METHODS))


def test_the_reply_never_carries_the_session_token():
    """The point of moving the exchange into the daemon. These pages are served over plain HTTP
    with no CSP, and a downloaded agent's UI is a stranger's code — it learns THAT someone is
    signed in, never the credential."""
    gateway = _gateway(accounts=_Accounts(), tokens=_Tokens())
    reply = asyncio.run(gateway._auth_login({"email": "a@b.c", "password": "pw"}))
    assert reply["signedIn"] is True
    assert reply["email"] == "a@b.c"
    assert reply["accountId"] == "acct_1"
    assert "sess_abc" not in str(reply), "the credential itself never crosses the wire"


def test_status_says_whether_a_login_can_be_offered_at_all():
    gateway = _gateway(accounts=_Accounts(available=False), tokens=_Tokens())
    assert gateway._auth_status() == {
        "available": False,
        "signedIn": False,
        "email": "",
        "accountId": "",
    }


def test_status_reports_who_is_signed_in():
    gateway = _gateway(accounts=_Accounts(), tokens=_Tokens())
    asyncio.run(gateway._auth_login({"email": "a@b.c", "password": "pw"}))
    assert gateway._auth_status() == {
        "available": True,
        "signedIn": True,
        "email": "a@b.c",
        "accountId": "acct_1",
    }


def test_logging_out_over_the_wire_clears_it():
    gateway = _gateway(accounts=_Accounts(), tokens=_Tokens())
    asyncio.run(gateway._auth_login({"email": "a@b.c", "password": "pw"}))
    assert gateway._auth_logout() == {"signedIn": False, "email": "", "accountId": ""}
    assert gateway._auth_status()["signedIn"] is False


# ── publishing asks who you are, not who is paying ─────────────────────────
def test_publishing_accepts_the_signed_in_identity_with_no_billing_key(monkeypatch):
    """The refusal this removes: "you are not signed in", shown to somebody who just signed in.
    Publishing looked for the MODEL-PROXY key, so the question it actually asked was "are you a
    paying customer" — and an author on their own API keys could never publish."""
    from agent_runtime.infrastructure.marketplace.http_publisher import platform_session_token

    monkeypatch.delenv("AGENTD_MODEL_PROXY_KEY", raising=False)
    monkeypatch.delenv("AGENTD_MODEL_GATEWAY_KEY", raising=False)
    monkeypatch.setenv(TOKEN_KEY, "sess_identity")

    class _Cfg:
        model_proxy = {}

    assert platform_session_token(_Cfg()) == "sess_identity"


def test_the_identity_token_wins_over_the_billing_key(monkeypatch):
    from agent_runtime.infrastructure.marketplace.http_publisher import platform_session_token

    monkeypatch.setenv(TOKEN_KEY, "sess_identity")
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "pays-for-models")

    class _Cfg:
        model_proxy = {}

    assert platform_session_token(_Cfg()) == "sess_identity"


def test_an_install_that_signed_in_the_old_way_still_publishes(monkeypatch):
    """Nobody gets signed out by this change: an install holding only the old credential keeps
    working until it next signs in."""
    from agent_runtime.infrastructure.marketplace.http_publisher import platform_session_token

    monkeypatch.delenv(TOKEN_KEY, raising=False)
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "legacy-token")

    class _Cfg:
        model_proxy = {}

    assert platform_session_token(_Cfg()) == "legacy-token"


def test_a_daemon_built_without_the_service_says_so():
    """Rather than a 'NoneType has no attribute' traceback in a client's error frame."""
    from agent_runtime.presentation.gateway import Gateway

    class _Cfg:
        accounts = {}
        distribution = None
        state_dir = "."

    with pytest.raises(ValueError, match="sign-in service"):
        Gateway(config=_Cfg(), service=None, sign_in=None)._auth_status()
