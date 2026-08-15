"""PKCE flow state, and the external-login endpoints.

The provider itself is not exercised over the network here — that would be testing Google. What is
tested is everything WE own and could get wrong: the single-use flow state, the expiry, the CSRF
`state` check, and that the endpoints stay absent rather than half-present when no provider is
configured.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from identity.application.services.oauth_flow import (
    FLOW_TTL_S,
    MAX_PENDING,
    OAuthFlowStore,
    pkce_pair,
)
from identity.domain.errors import AuthenticationFailed, IdentityConfigurationError

ACCOUNTS_APP = Path(__file__).resolve().parents[2] / "accounts" / "app.py"
ISSUER = "https://accounts.test.invalid"


def test_pkce_challenge_is_the_s256_of_the_verifier():
    """`plain` would make PKCE decorative — the challenge must be a real digest."""
    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert challenge == expected
    assert verifier != challenge


def test_pkce_pairs_are_unique():
    assert len({pkce_pair()[0] for _ in range(50)}) == 50


def test_a_flow_is_single_use():
    """A replayed callback must find nothing — otherwise one intercepted code works twice."""
    store = OAuthFlowStore()
    flow = store.begin(provider="google", redirect_uri="http://127.0.0.1:1/cb")
    assert store.consume(flow["state"])["nonce"] == flow["nonce"]
    with pytest.raises(AuthenticationFailed):
        store.consume(flow["state"])


def test_an_unknown_state_is_refused():
    """The CSRF check: a callback carrying a state we never issued is someone else's redirect."""
    store = OAuthFlowStore()
    with pytest.raises(AuthenticationFailed):
        store.consume("state-we-never-made")


def test_a_stale_flow_expires():
    now = [1000.0]
    store = OAuthFlowStore(clock=lambda: now[0])
    flow = store.begin(provider="google", redirect_uri="http://127.0.0.1:1/cb")
    now[0] += FLOW_TTL_S + 1
    with pytest.raises(AuthenticationFailed):
        store.consume(flow["state"])


def test_pending_flows_are_capped():
    """An unauthenticated endpoint that allocates memory needs a ceiling."""
    store = OAuthFlowStore()
    for _ in range(MAX_PENDING):
        store.begin(provider="google", redirect_uri="http://127.0.0.1:1/cb")
    with pytest.raises(AuthenticationFailed):
        store.begin(provider="google", redirect_uri="http://127.0.0.1:1/cb")


def test_a_public_client_keeps_its_own_verifier():
    """The desktop generates its own challenge; the verifier must never reach the server."""
    store = OAuthFlowStore()
    _, challenge = pkce_pair()
    flow = store.begin(provider="google", redirect_uri="http://127.0.0.1:1/cb", code_challenge=challenge)
    assert flow["challenge"] == challenge


def test_expired_flows_are_swept():
    now = [1000.0]
    store = OAuthFlowStore(clock=lambda: now[0])
    for _ in range(10):
        store.begin(provider="google", redirect_uri="http://127.0.0.1:1/cb")
    now[0] += FLOW_TTL_S + 1
    store.begin(provider="google", redirect_uri="http://127.0.0.1:1/cb")
    assert len(store._flows) == 1, "abandoned flows accumulate forever"


# --- provider construction ---------------------------------------------------------------


def test_an_oidc_provider_needs_its_credentials():
    from identity.infrastructure.oidc_provider import OidcProvider

    with pytest.raises(IdentityConfigurationError):
        OidcProvider(name="google", discovery_url="", client_id="")


def test_providers_are_built_from_the_environment(monkeypatch):
    """Adding Microsoft is configuration, not code — this is the assertion that proves it."""
    from identity.infrastructure.oidc_provider import providers_from_env

    monkeypatch.setenv("AGENTD_OIDC_PROVIDERS", "google,microsoft")
    for name in ("GOOGLE", "MICROSOFT"):
        monkeypatch.setenv(f"AGENTD_OIDC_{name}_DISCOVERY", f"https://{name.lower()}/.well-known/openid-configuration")
        monkeypatch.setenv(f"AGENTD_OIDC_{name}_CLIENT_ID", f"{name.lower()}-client")
    built = providers_from_env()
    assert [p.name for p in built] == ["google", "microsoft"]


def test_oidc_provider_refuses_password_auth():
    """A pure external provider has no password to check; saying so loudly beats returning a
    confusing authentication failure."""
    from identity.infrastructure.oidc_provider import OidcProvider

    p = OidcProvider(name="google", discovery_url="https://x/.well-known", client_id="cid")
    with pytest.raises(IdentityConfigurationError):
        p.authenticate(email="a@b.c", password="x")


def test_oidc_deployment_without_providers_refuses_to_start(monkeypatch):
    from identity.main import identity_factory

    monkeypatch.setenv("AGENTD_IDENTITY_PROVIDER", "oidc")
    monkeypatch.delenv("AGENTD_OIDC_PROVIDERS", raising=False)
    with pytest.raises(IdentityConfigurationError):
        identity_factory.build_identity_provider(object())  # type: ignore[arg-type]


# --- the endpoints -----------------------------------------------------------------------


def _app(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("AGENTD_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "0/0")
    monkeypatch.setenv("AGENTD_AUTH_ISSUER", ISSUER)
    monkeypatch.delenv("AGENTD_IDENTITY_PROVIDER", raising=False)
    monkeypatch.delenv("AGENTD_OIDC_PROVIDERS", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("agentd_accounts_oauth_app", ACCOUNTS_APP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_provider_is_a_404(monkeypatch, tmp_path):
    module = _app(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        r = client.post("/auth/authorize", json={"provider": "google", "redirect_uri": "http://x/cb"})
        assert r.status_code == 404


def test_discovery_lists_only_configured_providers(monkeypatch, tmp_path):
    """The sign-in UI renders this list verbatim, so a build with no Google configured must not
    advertise a Google button that cannot work."""
    module = _app(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        doc = client.get("/.well-known/agentd-platform").json()
        assert [p["id"] for p in doc["providers"]] == ["local"]


def test_discovery_advertises_an_external_provider_when_configured(monkeypatch, tmp_path):
    module = _app(
        monkeypatch,
        tmp_path,
        AGENTD_OIDC_PROVIDERS="google",
        AGENTD_OIDC_GOOGLE_DISCOVERY="https://accounts.google.com/.well-known/openid-configuration",
        AGENTD_OIDC_GOOGLE_CLIENT_ID="cid",
    )
    with TestClient(module.app) as client:
        doc = client.get("/.well-known/agentd-platform").json()
        ids = [p["id"] for p in doc["providers"]]
        assert ids == ["local", "google"]
        google = next(p for p in doc["providers"] if p["id"] == "google")
        assert google["label"] == "Google" and google["kind"] == "oidc"


def test_callback_with_an_unknown_state_is_rejected(monkeypatch, tmp_path):
    module = _app(
        monkeypatch,
        tmp_path,
        AGENTD_OIDC_PROVIDERS="google",
        AGENTD_OIDC_GOOGLE_DISCOVERY="https://accounts.google.com/.well-known/openid-configuration",
        AGENTD_OIDC_GOOGLE_CLIENT_ID="cid",
    )
    with TestClient(module.app) as client:
        r = client.post("/auth/callback", json={"state": "forged", "code": "abc"})
        assert r.status_code == 400
