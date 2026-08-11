"""The two publishing adapters, and the rule that picks between them.

The point of this seam: an ordinary author cannot be handed the ed25519 key that signs the
marketplace, so the operator path (`s3://`, local key) and the author path (a publish service,
sign-in only) have to be different implementations of one contract. These tests pin the behaviour
that makes that safe — most importantly that a wrong-shaped target can never silently pick the
wrong one, and that "no target" publishes nowhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.application.interfaces.bundle_publisher import PublishRequest
from agent_runtime.infrastructure.marketplace.http_publisher import (
    HttpRegistryPublisher,
    platform_session_token,
)
from agent_runtime.infrastructure.marketplace.publisher_factory import (
    is_service_target,
    publisher_for,
)
from agent_runtime.infrastructure.marketplace.s3_publisher import S3RegistryPublisher


class FakeConfig:
    def __init__(self, publish_target="", publisher_keyfile="", model_proxy=None):
        self.publish_target = publish_target
        self.publisher_keyfile = publisher_keyfile
        self.model_proxy = model_proxy or {}
        self.state_dir = "/tmp/state"


class FakePacker:
    """Writes a REAL .agentpkg, because the publisher reads its manifest back."""

    def __init__(self, bundle_id="weather", version="1.2.0"):
        self.bundle_id, self.version = bundle_id, version
        self.calls = []

    def pack(self, agent_dir, out_dir, version=""):
        import zipfile

        self.calls.append((agent_dir, version))
        target = Path(out_dir) / f"{self.bundle_id}-{self.version}.agentpkg"
        with zipfile.ZipFile(target, "w") as zf:
            zf.writestr(
                "bundle.toml",
                f'[bundle]\nid = "{self.bundle_id}"\nname = "Weather"\nversion = "{self.version}"\n',
            )
        return target


@pytest.fixture(autouse=True)
def no_ambient_token(monkeypatch):
    """This machine's own sign-in must not decide what these tests assert."""
    monkeypatch.delenv("AGENTD_MODEL_PROXY_KEY", raising=False)
    monkeypatch.delenv("AGENTD_MODEL_GATEWAY_KEY", raising=False)


# ── choosing an adapter ─────────────────────────────────────────────────────────────────


def test_the_target_scheme_picks_the_adapter():
    config = FakeConfig()
    assert isinstance(publisher_for(config, "https://api.example.com"), HttpRegistryPublisher)
    assert isinstance(publisher_for(config, "http://localhost:8080"), HttpRegistryPublisher)
    assert isinstance(publisher_for(config, "s3://bucket/prefix"), S3RegistryPublisher)
    assert isinstance(publisher_for(config, "/srv/registry"), S3RegistryPublisher)


def test_no_target_means_no_publisher():
    """The empty default is what stops a downloaded copy publishing into someone else's store."""
    assert publisher_for(FakeConfig(), "") is None


def test_config_supplies_the_target_when_no_override():
    publisher = publisher_for(FakeConfig(publish_target="https://api.example.com"))
    assert isinstance(publisher, HttpRegistryPublisher)


def test_is_service_target_is_case_and_space_tolerant():
    assert is_service_target("  HTTPS://api.example.com ")
    assert not is_service_target("s3://bucket")
    assert not is_service_target("")


# ── the OPERATOR adapter states what it needs ────────────────────────────────────────────


def test_operator_publisher_requires_a_key_it_can_find(tmp_path):
    assert any("publisher_keyfile" in r for r in S3RegistryPublisher("s3://b").requirements())

    missing_file = S3RegistryPublisher("s3://b", str(tmp_path / "nope.json"))
    assert any("does not exist" in r for r in missing_file.requirements())

    keyfile = tmp_path / "key.json"
    keyfile.write_text("{}")
    assert S3RegistryPublisher("s3://b", str(keyfile)).requirements() == []


def test_operator_publisher_never_rotates_a_key_or_publishes_unsigned(monkeypatch, tmp_path):
    """Both would break every already-installed client, so a TOOL must not be able to choose them."""
    seen = {}

    def fake_run_publish(args):
        seen.update(vars(args))
        return 0

    from agent_runtime.cli.commands import bundle as bundle_cli

    monkeypatch.setattr(bundle_cli, "run_publish", fake_run_publish)
    keyfile = tmp_path / "key.json"
    keyfile.write_text("{}")

    S3RegistryPublisher("s3://b", str(keyfile)).publish(
        PublishRequest(agent_dir=tmp_path, dry_run=False)
    )
    assert seen["rotate_key"] is False
    assert seen["unsigned"] is False
    # Publishing the ENGINE is a release job; a per-agent publish must never touch it.
    assert seen["engine"] == ""


def test_operator_publisher_passes_the_installer_choice_through(monkeypatch, tmp_path):
    seen = {}
    from agent_runtime.cli.commands import bundle as bundle_cli

    monkeypatch.setattr(bundle_cli, "run_publish", lambda args: seen.update(vars(args)) or 0)
    keyfile = tmp_path / "key.json"
    keyfile.write_text("{}")
    publisher = S3RegistryPublisher("s3://b", str(keyfile))

    publisher.publish(PublishRequest(agent_dir=tmp_path, with_installer=False))
    assert seen["no_installers"] is True
    publisher.publish(PublishRequest(agent_dir=tmp_path, with_installer=True))
    assert seen["no_installers"] is False


# ── the AUTHOR adapter needs sign-in, NOT a key ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """These tests describe PRECEDENCE, so the environment has to be STATED, not inherited.

    They were passing by luck: the machines running them happened to have no platform credentials
    in their `.env`. Once identity got a key of its own — AGENTD_SESSION_TOKEN, written at sign-in
    and loaded into the environment at boot — any developer who had signed in started seeing three
    failures here that had nothing to do with whatever they were working on.
    """
    for name in ("AGENTD_MODEL_PROXY_KEY", "AGENTD_MODEL_GATEWAY_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_the_connections_account_is_enough_to_publish():
    """Signing in is the requirement, not being a paying customer. Identity comes from the TURN's
    account — the one mechanism on a laptop and a shared server alike."""
    from agent_runtime.infrastructure import accounts

    token = accounts.set_account({"account_id": "a1", "session_token": "sess_conn"})
    try:
        assert HttpRegistryPublisher("https://x", FakeConfig(), FakePacker()).requirements() == []
    finally:
        accounts.reset_account(token)


def test_author_publisher_asks_for_sign_in_and_never_for_a_key():
    missing = HttpRegistryPublisher("https://api.example.com", FakeConfig(), FakePacker()).requirements()
    assert any("not signed in" in r for r in missing)
    # The whole point: the author is told they need no signing key.
    assert any("no signing key is needed" in r for r in missing)
    assert not any("publisher_keyfile" in r for r in missing)


def test_author_publisher_is_ready_once_signed_in(monkeypatch):
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "sess_abc")
    assert HttpRegistryPublisher("https://x", FakeConfig(), FakePacker()).requirements() == []


def test_session_token_precedence_prefers_the_connected_account(monkeypatch):
    """On a multi-tenant daemon the per-connection account wins — publishing as the wrong user
    would be the worst possible bug in that file."""
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "the-machines-token")
    from agent_runtime.infrastructure import accounts

    token = accounts.current_account.set({"account_id": "a1", "session_token": "the-users-token"})
    try:
        assert platform_session_token(FakeConfig()) == "the-users-token"
    finally:
        accounts.current_account.reset(token)
    assert platform_session_token(FakeConfig()) == "the-machines-token"


def test_session_token_falls_back_to_the_configured_proxy_key():
    config = FakeConfig(model_proxy={"api_key": "from-config"})
    assert platform_session_token(config) == "from-config"


def test_a_dry_run_sends_nothing_and_says_what_it_would_send(tmp_path):
    packer = FakePacker()
    publisher = HttpRegistryPublisher("https://api.example.com", FakeConfig(), packer)

    result = publisher.publish(PublishRequest(agent_dir=tmp_path, dry_run=True))

    assert result.ok and result.dry_run
    assert result.bundle_id == "weather" and result.version == "1.2.0"
    assert "POST https://api.example.com/registry/publish" in result.detail
    assert "weather-1.2.0.agentpkg" in result.detail


def test_a_dry_run_without_a_stub_builder_warns_about_the_missing_installer(tmp_path):
    result = HttpRegistryPublisher("https://x", FakeConfig(), FakePacker()).publish(
        PublishRequest(agent_dir=tmp_path, dry_run=True)
    )
    assert any("stranger with a bare machine" in w for w in result.warnings)


def test_a_failing_stub_build_still_publishes_the_bundle(tmp_path):
    """A missing installer is a DEGRADED publish, never a lost one."""

    def explode(_agent_dir):
        raise RuntimeError("makensis died")

    result = HttpRegistryPublisher("https://x", FakeConfig(), FakePacker(), explode).publish(
        PublishRequest(agent_dir=tmp_path, dry_run=True)
    )
    assert result.ok
    assert any("makensis died" in w for w in result.warnings)


def test_the_installer_is_listed_in_the_preview_when_one_was_built(tmp_path):
    stub = tmp_path / "weather-1.2.0-setup.exe"
    stub.write_bytes(b"MZ")
    result = HttpRegistryPublisher(
        "https://x", FakeConfig(), FakePacker(), lambda _d: stub
    ).publish(PublishRequest(agent_dir=tmp_path, dry_run=True))
    assert "weather-1.2.0-setup.exe" in result.detail
    assert result.warnings == []


# ── server responses the author has to be able to act on ─────────────────────────────────


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def post_returning(monkeypatch, response):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: response)


def publish_real(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "sess_abc")
    publisher = HttpRegistryPublisher("https://api.example.com", FakeConfig(), FakePacker())
    return publisher.publish(PublishRequest(agent_dir=tmp_path, dry_run=False))


def test_202_pending_review_is_a_success_not_an_error(tmp_path, monkeypatch):
    """A creator's FIRST publish files for admission. Calling that a failure sends the author
    hunting for a bug in an agent that is fine."""
    post_returning(monkeypatch, FakeResponse(202, {}))
    result = publish_real(tmp_path, monkeypatch)
    assert result.ok and result.pending
    assert "awaiting review" in result.message


def test_200_reports_the_urls_a_person_can_open(tmp_path, monkeypatch):
    post_returning(
        monkeypatch,
        FakeResponse(
            200,
            {
                "bundle_id": "weather",
                "version": "1.2.0",
                "url": "https://cdn/weather-1.2.0.agentpkg",
                "installer_url": "https://cdn/weather-1.2.0-setup.exe",
            },
        ),
    )
    result = publish_real(tmp_path, monkeypatch)
    assert result.ok and not result.pending
    assert result.installer_url.endswith("-setup.exe")


def test_409_explains_the_version_rule(tmp_path, monkeypatch):
    post_returning(monkeypatch, FakeResponse(409, {}))
    result = publish_real(tmp_path, monkeypatch)
    assert not result.ok
    assert "supersede BY VERSION" in result.message


def test_the_service_message_wins_over_the_generic_explanation(tmp_path, monkeypatch):
    post_returning(monkeypatch, FakeResponse(409, {"message": "bundle id owned by @someone-else"}))
    assert publish_real(tmp_path, monkeypatch).message == "bundle id owned by @someone-else"


def test_401_tells_the_author_to_sign_in_again(tmp_path, monkeypatch):
    post_returning(monkeypatch, FakeResponse(401, {}))
    assert "session expired" in publish_real(tmp_path, monkeypatch).message


def test_an_unreachable_service_is_a_result_not_a_traceback(tmp_path, monkeypatch):
    import httpx

    monkeypatch.setenv("AGENTD_MODEL_PROXY_KEY", "sess_abc")

    def boom(*a, **k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "post", boom)
    result = HttpRegistryPublisher("https://api.example.com", FakeConfig(), FakePacker()).publish(
        PublishRequest(agent_dir=tmp_path, dry_run=False)
    )
    assert not result.ok
    assert "could not reach" in result.message and "Nothing was published" in result.message


def test_a_non_json_error_body_is_still_explained(tmp_path, monkeypatch):
    post_returning(monkeypatch, FakeResponse(500, None, text="<html>gateway error</html>"))
    result = publish_real(tmp_path, monkeypatch)
    assert not result.ok
    assert "HTTP 500" in result.message
    assert "gateway error" in result.detail
