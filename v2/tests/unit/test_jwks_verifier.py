"""Local token verification — the change that takes the accounts service off the hot path.

The interesting assertions are NOT "a good token verifies". They are the ones that keep a local
verifier from becoming a weaker check than the round trip it replaces:

  * the algorithm comes from the resolved KEY, never from the token header (JWT confusion);
  * the issuer and audience are always checked (a dev token must not spend on prod, and a token
    minted for the daemon must not be spendable at the proxy);
  * an unknown key id cannot be used to hammer the JWKS endpoint;
  * and the happy path performs NO network I/O at all, which is the entire point.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from identity.domain.errors import TokenExpired, TokenInvalid
from identity.domain.principal import Principal
from identity.infrastructure.jwks_verifier import JwksVerifier, looks_like_jwt
from identity.infrastructure.jwt_token_issuer import JwtTokenIssuer
from identity.infrastructure.sqlite_key_store import SqliteKeyStore
from identity.infrastructure.sqlite_schema import create_schema

ISSUER = "https://accounts.test.invalid"
JWKS_URI = "https://accounts.test.invalid/auth/jwks.json"
AUDIENCE = ("agentd-daemon", "agentd-proxy")


@pytest.fixture
def stack():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY)")
    create_schema(conn)
    keys = SqliteKeyStore(conn)
    issuer = JwtTokenIssuer(keys, issuer=ISSUER, access_ttl_s=600, audience=AUDIENCE)
    return issuer, keys


def _verifier(issuer_obj, monkeypatch, *, audience="agentd-proxy", iss=ISSUER, tmp_path=None):
    """A verifier wired to an in-process 'network' that serves the real JWKS."""
    calls = {"n": 0}

    def fake_get(url, timeout=0):  # noqa: ARG001
        calls["n"] += 1
        from types import SimpleNamespace

        return SimpleNamespace(status_code=200, json=lambda: issuer_obj.public_jwks())

    import httpx

    monkeypatch.setattr(httpx, "get", fake_get)
    v = JwksVerifier(
        jwks_uri=JWKS_URI,
        issuer=iss,
        audience=audience,
        cache_path=(tmp_path / "jwks.json") if tmp_path else None,
    )
    return v, calls


def test_a_real_token_verifies_locally(stack, monkeypatch):
    issuer, _ = stack
    token, _ = issuer.issue(Principal(account_id="acct_1", email="a@b.c", scopes=("chat",)))
    v, _ = _verifier(issuer, monkeypatch)
    claims = v.verify(token)
    assert claims.account_id == "acct_1"
    assert claims.scopes == ("chat",)


def test_happy_path_makes_no_network_call_after_the_first(stack, monkeypatch):
    """The whole point: verification must be pure CPU, or we have swapped one round trip for
    another and gained nothing."""
    issuer, _ = stack
    v, calls = _verifier(issuer, monkeypatch)
    for i in range(50):
        token, _ = issuer.issue(Principal(account_id=f"acct_{i}"))
        v.verify(token)
    assert calls["n"] == 1


def test_wrong_issuer_is_refused(stack, monkeypatch):
    """A token from another deployment. THE cross-environment guard."""
    issuer, _ = stack
    token, _ = issuer.issue(Principal(account_id="acct_1"))
    v, _ = _verifier(issuer, monkeypatch, iss="https://other.invalid")
    with pytest.raises(TokenInvalid) as e:
        v.verify(token)
    assert "another deployment" in str(e.value)


def test_wrong_audience_is_refused(stack, monkeypatch):
    """A token minted for a different service must not be accepted here — this is what stops a
    narrow, downscoped token from being spent at full power somewhere else."""
    issuer, _ = stack
    token, _ = issuer.issue(Principal(account_id="acct_1"), audience=("agentd-daemon",))
    v, _ = _verifier(issuer, monkeypatch, audience="agentd-proxy")
    with pytest.raises(TokenInvalid):
        v.verify(token)


def test_expired_token_raises_the_distinct_error(stack, monkeypatch):
    """Expired must not collapse into invalid: the client's correct reaction differs (refresh
    and retry, vs sign in again)."""
    issuer_keys = stack[1]
    stale = JwtTokenIssuer(
        issuer_keys, issuer=ISSUER, access_ttl_s=600, audience=AUDIENCE,
        clock=lambda: time.time() - 86_400,
    )
    token, _ = stale.issue(Principal(account_id="acct_1"))
    v, _ = _verifier(stack[0], monkeypatch)
    with pytest.raises(TokenExpired):
        v.verify(token)


def test_a_token_signed_by_a_foreign_key_is_refused(stack, monkeypatch):
    """Right issuer and audience, wrong signer. The signature is the only thing that matters."""
    issuer, _ = stack
    other_conn = sqlite3.connect(":memory:")
    other_conn.row_factory = sqlite3.Row
    other_conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY)")
    create_schema(other_conn)
    rogue = JwtTokenIssuer(
        SqliteKeyStore(other_conn), issuer=ISSUER, access_ttl_s=600, audience=AUDIENCE
    )
    token, _ = rogue.issue(Principal(account_id="acct_evil"))

    v, _ = _verifier(issuer, monkeypatch)
    with pytest.raises(TokenInvalid):
        v.verify(token)


def test_unknown_kid_cannot_hammer_the_jwks_endpoint(stack, monkeypatch):
    """Without a refetch floor, a hostile client turns random key ids into a fetch amplifier
    against our own identity service."""
    issuer, _ = stack
    v, calls = _verifier(issuer, monkeypatch)
    token, _ = issuer.issue(Principal(account_id="acct_1"))
    v.verify(token)  # warms the cache (1 fetch)

    import jwt as pyjwt

    forged = pyjwt.encode({"sub": "x"}, "secret", algorithm="HS256", headers={"kid": "nope"})
    for _ in range(20):
        with pytest.raises(TokenInvalid):
            v.verify(forged)
    assert calls["n"] == 1, "an unknown kid triggered repeated fetches"


def test_key_rotation_keeps_old_tokens_verifiable(stack, monkeypatch):
    """Rotation must not sign everyone out: a token minted a moment before the rotation has to
    stay valid until it expires naturally."""
    issuer, keys = stack
    before, _ = issuer.issue(Principal(account_id="acct_1"))
    keys.rotate(retire_after_s=3600)
    after, _ = issuer.issue(Principal(account_id="acct_1"))

    v, _ = _verifier(issuer, monkeypatch)
    assert v.verify(after).account_id == "acct_1"
    # A NEW verifier, because the first cached the pre-rotation key set.
    v2, _ = _verifier(issuer, monkeypatch)
    assert v2.verify(before).account_id == "acct_1"


def test_disk_cache_survives_a_restart_with_the_platform_down(stack, monkeypatch, tmp_path):
    """A daemon that boots while the platform is unreachable must still verify tokens its users
    already hold."""
    issuer, _ = stack
    token, _ = issuer.issue(Principal(account_id="acct_1"))
    v, _ = _verifier(issuer, monkeypatch, tmp_path=tmp_path)
    v.verify(token)

    import httpx

    def boom(*_a, **_k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", boom)
    restarted = JwksVerifier(
        jwks_uri=JWKS_URI, issuer=ISSUER, audience="agentd-proxy",
        cache_path=tmp_path / "jwks.json",
    )
    assert restarted.verify(token).account_id == "acct_1"


def test_disk_cache_from_another_deployment_is_ignored(stack, monkeypatch, tmp_path):
    """Cached keys are a TRUST ANCHOR, so the wrong deployment's copy is worse than none."""
    issuer, _ = stack
    token, _ = issuer.issue(Principal(account_id="acct_1"))
    v, _ = _verifier(issuer, monkeypatch, tmp_path=tmp_path)
    v.verify(token)

    elsewhere = JwksVerifier(
        jwks_uri="https://other.invalid/auth/jwks.json", issuer=ISSUER,
        audience="agentd-proxy", cache_path=tmp_path / "jwks.json",
    )
    assert elsewhere._keys == {}


def test_unconfigured_verifier_refuses_everything(stack):
    v = JwksVerifier(jwks_uri="", issuer="")
    assert not v.configured
    with pytest.raises(TokenInvalid):
        v.verify("anything")


def test_looks_like_jwt_routes_correctly():
    """Routing only — a legacy opaque token must never take the JWT path, and vice versa."""
    assert looks_like_jwt("a.b.c")
    assert not looks_like_jwt("sess_abc")
    assert not looks_like_jwt("")
    assert not looks_like_jwt("not-a-token")
