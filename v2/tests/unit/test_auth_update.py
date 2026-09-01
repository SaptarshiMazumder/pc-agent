"""Keeping an OPEN socket authenticated as its access token rolls over.

THE PROBLEM. An access token lives ten minutes; a socket lives hours, and an agent run can outlive
the token that started it. The credential is presented once, on the connect URL — so without a way
to replace it in place, the only option is rebuilding the socket every ten minutes, which drops
in-flight runs and re-subscribes every stream, forever.

`auth.update` swaps it. The rules that make that safe are what these tests pin:

  * a token that does not verify is REFUSED, and the connection keeps the identity it had (a
    failed refresh must not silently downgrade a signed-in socket to anonymous);
  * a valid token for a DIFFERENT account is refused too — this connection's runs, subscriptions
    and pinned state belong to whoever opened it, and re-pointing them mid-stream would leak one
    user's events to another.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.infrastructure import accounts
from agent_runtime.presentation.gateway import Gateway
from agent_runtime.presentation.protocol import Request
from identity.domain.principal import Principal
from identity.infrastructure.jwks_verifier import JwksVerifier
from identity.infrastructure.jwt_token_issuer import JwtTokenIssuer
from identity.infrastructure.sqlite_key_store import SqliteKeyStore
from identity.infrastructure.sqlite_schema import create_schema

ISSUER = "https://accounts.test.invalid"


@pytest.fixture
def signer():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY)")
    create_schema(conn)
    return JwtTokenIssuer(
        SqliteKeyStore(conn), issuer=ISSUER, access_ttl_s=600,
        audience=("agentd-daemon", "agentd-proxy"),
    )


@pytest.fixture(autouse=True)
def wire_verifier(signer, monkeypatch):
    """Point the accounts seam at an in-process verifier fed by our own signer.

    Both fetch paths are stubbed, not just the sync one: `accounts.resolve` is async and takes
    `_fetch_async`, so patching only `httpx.get` leaves the verifier making a real DNS lookup and
    every token failing with 'unknown signing key' — which reads exactly like a signing bug.
    """
    verifier = JwksVerifier(
        jwks_uri="https://accounts.test.invalid/auth/jwks.json",
        issuer=ISSUER,
        audience="agentd-daemon",
    )

    async def fetch_async(_self=None):
        return signer.public_jwks()

    monkeypatch.setattr(verifier, "_fetch_sync", lambda: signer.public_jwks())
    monkeypatch.setattr(verifier, "_fetch_async", fetch_async)
    monkeypatch.setattr(accounts, "_verifier", verifier)
    monkeypatch.setattr(accounts, "_api_base", "")
    accounts._resolve_cache.clear()
    yield
    accounts._resolve_cache.clear()


def _gateway() -> Gateway:
    return Gateway.__new__(Gateway)


def _token(signer, account_id: str, *, ttl_offset: float = 0.0) -> str:
    issuer = signer
    if ttl_offset:
        issuer = JwtTokenIssuer(
            signer._keys, issuer=ISSUER, access_ttl_s=600,
            audience=("agentd-daemon", "agentd-proxy"),
            clock=lambda: time.time() + ttl_offset,
        )
    token, _ = issuer.issue(Principal(account_id=account_id, email=f"{account_id}@x.test"))
    return token


@pytest.mark.asyncio
async def test_a_fresh_token_replaces_the_connections_credential(signer):
    gw = _gateway()
    old = await accounts.resolve(_token(signer, "acct_1"))
    new_token = _token(signer, "acct_1")

    account, response = await gw._auth_update(
        Request(id="1", method="auth.update", params={"accessToken": new_token}), old
    )
    assert response.ok
    assert account is not None and account["session_token"] == new_token
    assert account["account_id"] == "acct_1"


@pytest.mark.asyncio
async def test_an_invalid_token_leaves_the_connection_as_it_was(signer):
    """A failed refresh must not silently sign the socket out."""
    gw = _gateway()
    old = await accounts.resolve(_token(signer, "acct_1"))

    account, response = await gw._auth_update(
        Request(id="1", method="auth.update", params={"accessToken": "ey.garbage.x"}), old
    )
    assert not response.ok
    assert response.payload["code"] == "auth_invalid"
    assert account is old, "the connection lost its identity on a bad update"


@pytest.mark.asyncio
async def test_a_token_for_another_account_is_refused(signer):
    """Re-pointing a live connection at a different account would leak one user's events to
    another. Switching accounts is a reconnect."""
    gw = _gateway()
    mine = await accounts.resolve(_token(signer, "acct_1"))
    theirs = _token(signer, "acct_2")

    account, response = await gw._auth_update(
        Request(id="1", method="auth.update", params={"accessToken": theirs}), mine
    )
    assert not response.ok
    assert response.payload["code"] == "auth_account_mismatch"
    assert account is mine


@pytest.mark.asyncio
async def test_a_missing_token_is_a_plain_error(signer):
    gw = _gateway()
    old = await accounts.resolve(_token(signer, "acct_1"))
    account, response = await gw._auth_update(
        Request(id="1", method="auth.update", params={}), old
    )
    assert not response.ok and account is old


@pytest.mark.asyncio
async def test_an_anonymous_connection_can_become_signed_in(signer):
    """Signing in on an already-open socket: there is no prior account to conflict with, so the
    update simply installs one. This is what lets a desktop window sign in without reconnecting."""
    gw = _gateway()
    account, response = await gw._auth_update(
        Request(id="1", method="auth.update", params={"accessToken": _token(signer, "acct_9")}),
        None,
    )
    assert response.ok and account is not None
    assert account["account_id"] == "acct_9"


@pytest.mark.asyncio
async def test_resolve_reports_the_expiry_so_a_turn_can_be_refused(signer):
    """The turn-level guard needs to know WHEN the credential dies; without it the failure
    surfaces as an opaque provider error halfway through a run."""
    account = await accounts.resolve(_token(signer, "acct_1"))
    assert account is not None
    assert account["expires_at"] > time.time()
    assert account["verified"] == "local"


@pytest.mark.asyncio
async def test_an_expired_token_does_not_resolve(signer):
    stale = _token(signer, "acct_1", ttl_offset=-86_400)
    assert await accounts.resolve(stale) is None
