"""Contract tests for the identity module.

Loaded by path like tests/unit/test_accounts_service.py, because ``accounts/`` is a standalone
service directory rather than an importable package, and because a fresh module per test gives
each one its own database and its own rate-limiter state.

What these pin, in priority order:

  1. NON-DESTRUCTIVENESS. An account that existed before identity did must sign in to the SAME
     ``acct_`` id. That is the single assertion the whole migration rests on: the id is the token
     subject, the key of every usage row and credit grant, and the name of the account's state
     directory. If it changes, a user silently loses their credits and their chat history.
  2. ONE credential kind. The opaque `sess_` session is gone; `/login` and `/auth/login` return
     the same signed token, and `/resolve` answers for it with the shape the daemon, the model
     proxy, ingest and the publish authenticator already expect.
  3. Refresh rotation, and that reuse REALLY revokes the family (it is easy to write a version
     that detects theft and then rolls the revocation back — see the transaction note in
     identity/presentation/auth_router.py).
"""

from __future__ import annotations

import importlib.util
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

V2 = Path(__file__).resolve().parents[2]
ACCOUNTS_APP = V2 / "accounts" / "app.py"

EMAIL = "user@example.com"
PASSWORD = "hunter2hunter2"
ISSUER = "https://accounts.test.invalid"


def _load(monkeypatch, tmp_path, **env: str):
    monkeypatch.setenv("AGENTD_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "0/0")
    monkeypatch.setenv("AGENTD_AUTH_ISSUER", ISSUER)
    monkeypatch.delenv("ACCOUNTS_INTERNAL_KEY", raising=False)
    monkeypatch.delenv("AGENTD_IDENTITY_KEK", raising=False)
    monkeypatch.delenv("AGENTD_IDENTITY_PROVIDER", raising=False)
    for key, value in env.items():
        if value == "":
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("agentd_accounts_identity_app", ACCOUNTS_APP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def app(monkeypatch, tmp_path):
    module = _load(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        yield client, module


def _signup(client, email=EMAIL, password=PASSWORD) -> str:
    r = client.post("/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["account_id"]


def _auth_login(client, email=EMAIL, password=PASSWORD) -> dict:
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


# --- the assertion the migration rests on ------------------------------------------------


def test_preexisting_account_signs_in_to_the_same_id(monkeypatch, tmp_path):
    """An account written BEFORE identity existed keeps its id, its credits and its history.

    Simulated exactly as the real world has it: a row in `accounts` with a PBKDF2 hash and NO
    identity record, because it was created by a build that had no identities table. If the
    startup backfill or the resolution order were wrong, the login below would mint a second
    account and this test would see a different id — with the original's credits stranded on an
    account nobody can reach.
    """
    db = tmp_path / "accounts.db"
    module = _load(monkeypatch, tmp_path)
    with TestClient(module.app):
        pass  # startup: creates the schema

    # Hand-write a legacy account + a credit grant, then drop its identity row so the account
    # looks exactly like one from before this feature.
    legacy_id = "acct_legacy0123456789"
    salt, pw_hash = module._make_pw(PASSWORD)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO accounts (id, email, pw_salt, pw_hash, budget_usd, active, created_at) "
        "VALUES (?, ?, ?, ?, NULL, 1, ?)",
        (legacy_id, EMAIL, salt, pw_hash, time.time()),
    )
    conn.execute(
        "INSERT INTO credit_grants (account_id, scope, credits, credits_used, credit_class, "
        "model_tier_max, expires_at, created_at) VALUES (?, 'platform', 500, 0, 'paid', '', 0, ?)",
        (legacy_id, time.time()),
    )
    conn.execute("DELETE FROM identities")
    conn.commit()
    conn.close()

    # Reboot: the startup backfill should adopt it.
    module2 = _load(monkeypatch, tmp_path)
    with TestClient(module2.app) as client:
        body = _auth_login(client)
        assert body["account_id"] == legacy_id, "a pre-existing account was given a NEW id"

        # ...and the credits are still reachable as that account.
        r = client.get("/me/credits", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert r.status_code == 200, r.text
        assert r.json()["credits_remaining"] == 500


def test_token_subject_is_the_account_id(app):
    client, module = app
    account_id = _signup(client)
    body = _auth_login(client)
    assert body["account_id"] == account_id

    # Decode without verifying — this asserts the CLAIM, not the verifier.
    import jwt

    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert claims["sub"] == account_id
    assert claims["iss"] == ISSUER
    assert "chat" in claims["scope"].split(" ")


# --- one credential kind ------------------------------------------------------------------


def test_login_and_auth_login_return_the_same_credential(app):
    """`/login` is now a thin alias of `/auth/login`, not a second kind of session.

    It used to ALSO mint an opaque `sess_` row so already-shipped clients kept working. That dual
    issuing is gone: one login endpoint handing out two kinds of credential is two things to
    revoke, two to expire, and two code paths to keep secure.
    """
    client, _ = app
    account_id = _signup(client)

    alias = client.post("/login", json={"email": EMAIL, "password": PASSWORD}).json()
    canonical = _auth_login(client)
    assert "token" not in alias, "the opaque session credential is still being minted"
    assert alias["access_token"] and alias["refresh_token"]

    for body in (alias, canonical):
        r = client.get("/resolve", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert r.status_code == 200
        assert r.json()["account_id"] == account_id


def test_a_sess_style_token_gets_nothing(app):
    """Nothing outside this repository ever held one, and nothing accepts one now."""
    client, _ = app
    _signup(client)
    assert client.get(
        "/resolve", headers={"Authorization": "Bearer sess_anything_at_all"}
    ).status_code == 401


def test_garbage_and_foreign_tokens_are_rejected(app):
    client, _ = app
    _signup(client)
    for bad in ("", "not-a-token", "ey.nope.nope", "sess_unknown"):
        r = client.get("/resolve", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401, bad


def test_a_token_from_another_stack_is_refused(app, monkeypatch, tmp_path):
    """The 'same email, two stacks, silently different accounts' guard.

    Two independent deployments have independent signing keys, so a foreign token fails on an
    unknown `kid` before the issuer is even considered. That is the stronger of the two defences
    and the one that fires in the real drifted-URL case; the issuer check below covers the other
    shape, where the key IS known.
    """
    client, _ = app
    _signup(client)
    other = _load(monkeypatch, tmp_path / "other", AGENTD_AUTH_ISSUER="https://other.invalid")
    with TestClient(other.app) as other_client:
        _signup(other_client)
        foreign = _auth_login(other_client)["access_token"]
    r = client.get("/resolve", headers={"Authorization": f"Bearer {foreign}"})
    assert r.status_code == 401
    assert "unknown signing key" in r.json()["detail"]


def test_a_token_whose_issuer_no_longer_matches_is_refused_by_name(monkeypatch, tmp_path):
    """Same database, same signing key, issuer reconfigured — a real operational event.

    Named explicitly in the error because a generic "invalid token" here sends someone hunting
    for a signing bug when the actual cause is a changed AGENTD_AUTH_ISSUER.
    """
    module = _load(monkeypatch, tmp_path, AGENTD_AUTH_ISSUER="https://before.invalid")
    with TestClient(module.app) as client:
        _signup(client)
        token = _auth_login(client)["access_token"]

    renamed = _load(monkeypatch, tmp_path, AGENTD_AUTH_ISSUER="https://after.invalid")
    with TestClient(renamed.app) as client:
        r = client.get("/resolve", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert "another deployment" in r.json()["detail"]


# --- refresh rotation --------------------------------------------------------------------


def test_refresh_rotates_and_old_token_dies(app):
    client, _ = app
    _signup(client)
    first = _auth_login(client)

    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200, second.text
    assert second.json()["refresh_token"] != first["refresh_token"], "token must ROTATE"
    assert second.json()["account_id"] == first["account_id"]


def test_refresh_reuse_revokes_the_whole_family(app):
    """Detection is not enough — the revocation has to survive the request that raises.

    The natural implementation rolls it back: the connection commits only on a clean exit and
    reuse detection raises. This test is the reason the transaction boundary sits outside the
    error mapping in auth_router.py.
    """
    client, _ = app
    _signup(client)
    first = _auth_login(client)
    rotated = client.post(
        "/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()["refresh_token"]

    reused = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert reused.status_code == 401
    assert "security" in reused.json()["detail"]

    # The legitimate holder's CURRENT token is dead too — we cannot tell which party is the thief.
    after = client.post("/auth/refresh", json={"refresh_token": rotated})
    assert after.status_code == 401, "the family survived a detected theft"


def test_logout_ends_the_session(app):
    client, _ = app
    _signup(client)
    body = _auth_login(client)
    assert client.post("/auth/logout", json={"refresh_token": body["refresh_token"]}).json()["ok"]
    assert client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 401


def test_logout_all_revokes_every_device(app):
    client, _ = app
    _signup(client)
    a = _auth_login(client)
    b = _auth_login(client)
    r = client.post("/auth/logout-all", headers={"Authorization": f"Bearer {a['access_token']}"})
    assert r.status_code == 200 and r.json()["revoked"] >= 2
    for pair in (a, b):
        assert client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401


def test_sessions_lists_one_entry_per_device_not_per_rotation(app):
    client, _ = app
    _signup(client)
    body = _auth_login(client)
    for _ in range(3):
        body = client.post(
            "/auth/refresh", json={"refresh_token": body["refresh_token"]}
        ).json()
    r = client.get("/auth/sessions", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert r.status_code == 200
    assert len(r.json()["sessions"]) == 1, "rotations must not each look like a new device"


# --- keys --------------------------------------------------------------------------------


def test_jwks_is_public_and_serves_a_usable_key(app):
    client, _ = app
    _signup(client)
    token = _auth_login(client)["access_token"]

    r = client.get("/auth/jwks.json")  # no credential — verifiers must be able to fetch it
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert keys and all(k.get("kid") and k.get("alg") and k.get("use") == "sig" for k in keys)
    assert all("d" not in k for k in keys), "a PRIVATE key half leaked into JWKS"

    # The published key really verifies a real token — this is the P2 contract for the daemon
    # and the model proxy, proven here so P2 is wiring rather than discovery.
    import jwt
    from jwt import PyJWK

    header = jwt.get_unverified_header(token)
    jwk = next(k for k in keys if k["kid"] == header["kid"])
    claims = jwt.decode(
        token,
        PyJWK.from_dict(jwk).key,
        algorithms=[jwk["alg"]],
        audience="agentd-proxy",
        issuer=ISSUER,
    )
    assert claims["sub"].startswith("acct_")


def test_expired_access_token_is_refused(monkeypatch, tmp_path):
    """Driven by minting a token whose `exp` is already in the past, NOT by moving the clock.

    PyJWT reads the current time through `datetime`, not `time.time`, so patching the latter
    changes what we issue and not what the verifier believes — which is exactly the asymmetry
    this test needs: an old token checked by a present-day verifier.
    """
    from identity.domain.errors import TokenExpired
    from identity.infrastructure.jwt_token_issuer import JwtTokenIssuer
    from identity.infrastructure.sqlite_key_store import SqliteKeyStore
    from identity.infrastructure.sqlite_schema import create_schema
    from identity.domain.principal import Principal

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY)")  # for the FK targets
    create_schema(conn)

    long_ago = time.time() - 86_400
    stale = JwtTokenIssuer(
        SqliteKeyStore(conn), issuer=ISSUER, access_ttl_s=600, clock=lambda: long_ago
    )
    token, _ = stale.issue(Principal(account_id="acct_x", scopes=("chat",)))

    verifier = JwtTokenIssuer(SqliteKeyStore(conn), issuer=ISSUER, access_ttl_s=600)
    with pytest.raises(TokenExpired):
        verifier.verify(token)


def test_expired_token_is_refused_over_http(monkeypatch, tmp_path):
    """The same thing through the real endpoint, so the 401 mapping is covered too."""
    module = _load(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        account_id = _signup(client)
        with sqlite3.connect(str(module.DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            from identity.domain.principal import Principal
            from identity.infrastructure.jwt_token_issuer import JwtTokenIssuer
            from identity.infrastructure.sqlite_key_store import SqliteKeyStore

            long_ago = time.time() - 86_400
            issuer = JwtTokenIssuer(
                SqliteKeyStore(conn), issuer=ISSUER, access_ttl_s=600, clock=lambda: long_ago
            )
            token, _ = issuer.issue(Principal(account_id=account_id))
        r = client.get("/resolve", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert "expired" in r.json()["detail"]


# --- configuration -----------------------------------------------------------------------


def test_without_an_issuer_sign_in_reports_that_clearly(monkeypatch, tmp_path):
    """No issuer configured is still a SUPPORTED state — a stack can be hibernating or have no
    public address yet — but there is no longer a second credential kind to fall back to. So the
    honest answer is a 503 naming the missing setting, not a 500 and not a weaker login.

    Deliberately NOT a startup crash: AGENTD_AUTH_ISSUER is derived from the stack's public
    address, so refusing to boot would turn a dormant environment into a broken one.
    """
    module = _load(monkeypatch, tmp_path, AGENTD_AUTH_ISSUER="")
    with TestClient(module.app) as client:
        _signup(client)  # accounts can still be created; they just cannot sign in yet
        r = client.post("/login", json={"email": EMAIL, "password": PASSWORD})
        assert r.status_code == 503
        assert "AGENTD_AUTH_ISSUER" in r.json()["detail"]
        assert client.post(
            "/auth/login", json={"email": EMAIL, "password": PASSWORD}
        ).status_code == 501
        assert client.get("/auth/jwks.json").json() == {"keys": []}


def test_unknown_provider_refuses_to_build(monkeypatch, tmp_path):
    """A typo in the provider name must not fall back to some other way of deciding who you are."""
    from identity.domain.errors import IdentityConfigurationError
    from identity.main import identity_factory

    monkeypatch.setenv("AGENTD_IDENTITY_PROVIDER", "loacl")
    with pytest.raises(IdentityConfigurationError):
        identity_factory.build_identity_provider(object())  # type: ignore[arg-type]


def test_signing_key_survives_a_restart(monkeypatch, tmp_path):
    """Tokens minted before a restart must still verify after it — the key is in the database,
    not in process memory, and this is what proves it."""
    module = _load(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        _signup(client)
        token = _auth_login(client)["access_token"]

    restarted = _load(monkeypatch, tmp_path)
    with TestClient(restarted.app) as client:
        assert client.get("/resolve", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_key_encryption_round_trips(monkeypatch, tmp_path):
    module = _load(monkeypatch, tmp_path, AGENTD_IDENTITY_KEK="a-real-secret")
    with TestClient(module.app) as client:
        _signup(client)
        token = _auth_login(client)["access_token"]
        assert client.get("/resolve", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    row = sqlite3.connect(str(tmp_path / "accounts.db")).execute(
        "SELECT encrypted, private_pem FROM signing_keys"
    ).fetchone()
    assert row[0] == 1 and "BEGIN PRIVATE KEY" not in row[1], "the private half was stored in clear"


# --- account rules -----------------------------------------------------------------------


def test_register_is_idempotent_about_duplicate_emails(app):
    client, _ = app
    _signup(client)
    r = client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 409


def test_register_enforces_the_password_minimum(app):
    client, _ = app
    r = client.post("/auth/register", json={"email": "new@example.com", "password": "short"})
    assert r.status_code == 400


def test_deactivated_account_cannot_sign_in_or_refresh(app, tmp_path):
    client, module = app
    account_id = _signup(client)
    body = _auth_login(client)

    with sqlite3.connect(str(module.DB_PATH)) as conn:
        conn.execute("UPDATE accounts SET active = 0 WHERE id = ?", (account_id,))

    assert client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 403
    # A 30-day refresh token must not outlive a deactivation — the account is re-read on refresh.
    assert client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 403


# --- signing UP (not just signing in) ---------------------------------------------------
# Every test above starts from `/signup` — the provisioning endpoint — and then logs in. The
# path a NEW WEB USER actually takes is `/auth/register`, and it was broken in production while
# this file was green: registration created the account, then PrincipalService.resolve failed to
# find it (no identity link exists yet — resolve writes the link only AFTER resolving) and fell
# through to creating it a SECOND time, which died on the row just inserted. The API answered
# 409 "there is already an account with that email" for every fresh address, the transaction
# rolled back, and no account existed afterwards. Nobody could sign up at all.


def test_register_creates_an_account_and_returns_a_usable_pair(app):
    client, _ = app
    r = client.post("/auth/register", json={"email": "newcomer@example.com", "password": PASSWORD})
    assert r.status_code == 200, r.text
    pair = r.json()
    assert pair.get("access_token") and pair.get("refresh_token")


def test_a_registered_account_can_log_in_afterwards(app):
    """The half that proved the 409 was spurious: registration reported a duplicate, and then
    login reported no such account. Both must be true together or neither is."""
    client, _ = app
    client.post("/auth/register", json={"email": "newcomer2@example.com", "password": PASSWORD})
    r = client.post("/auth/login", json={"email": "newcomer2@example.com", "password": PASSWORD})
    assert r.status_code == 200, r.text


def test_registering_the_same_email_twice_is_a_real_conflict(app):
    """The duplicate check still has to WORK — the fix must not turn a genuine collision into a
    second account or a silent sign-in."""
    client, _ = app
    first = client.post("/auth/register", json={"email": "twice@example.com", "password": PASSWORD})
    assert first.status_code == 200, first.text
    second = client.post("/auth/register", json={"email": "twice@example.com", "password": PASSWORD})
    assert second.status_code == 409, second.text


def test_register_then_login_resolve_to_one_account(app):
    """A duplicate account minted on login would split a user's chats, files and credits across
    two ids — silent, and unrecoverable once they have used both."""
    client, module = app
    reg = client.post("/auth/register", json={"email": "single@example.com", "password": PASSWORD})
    assert reg.status_code == 200, reg.text
    client.post("/auth/login", json={"email": "single@example.com", "password": PASSWORD})
    conn = sqlite3.connect(module.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id FROM accounts WHERE email = ?", ("single@example.com",)
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, f"expected one account, found {rows}"


# --- a session of one's own (derive) -----------------------------------------
# A window opened by the desktop app is handed an access token and no refresh token, so it cannot
# renew and goes anonymous ten minutes later — which the daemon does not refuse, so the user just
# watches their agents disappear. `derive` is how such a window stops being fed and mints a chain
# of its own instead.


def test_a_live_access_token_buys_a_session_of_its_own(app):
    client, _ = app
    _signup(client)
    first = _auth_login(client)

    r = client.post(
        "/auth/derive",
        json={"access_token": first["access_token"], "client_id": "app", "device_label": "Agent app"},
    )
    assert r.status_code == 200, r.text
    derived = r.json()

    assert derived["refresh_token"], "the whole point is that the window gets a key of its own"
    assert derived["account_id"] == first["account_id"]
    # A NEW CHAIN, not a copy. Two holders of one refresh token is not sharing — the second to
    # spend it looks exactly like theft, and the server kills the family and signs both out.
    assert derived["refresh_token"] != first["refresh_token"]


def test_the_two_sessions_rotate_independently(app):
    """Independence tested from the side that matters: spending one must not disturb the other."""
    client, _ = app
    _signup(client)
    first = _auth_login(client)
    derived = client.post("/auth/derive", json={"access_token": first["access_token"]}).json()

    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 200
    again = client.post("/auth/refresh", json={"refresh_token": derived["refresh_token"]})
    assert again.status_code == 200, "rotating the parent must not touch the derived session"


def test_a_derived_session_is_its_own_device(app):
    """It shows up as itself in the device list, which is what makes it revocable on its own
    rather than as a second, indistinguishable copy of the shell."""
    client, _ = app
    _signup(client)
    first = _auth_login(client)
    client.post(
        "/auth/derive",
        json={"access_token": first["access_token"], "client_id": "app", "device_label": "Agent app"},
    )

    r = client.get("/auth/sessions", headers={"Authorization": f"Bearer {first['access_token']}"})
    labels = [s.get("device_label", "") for s in r.json().get("sessions", [])]
    assert "Agent app" in labels


def test_logout_all_ends_derived_sessions_too(app):
    """Otherwise "sign out everywhere" would leave every agent window signed in — the worst kind
    of half-measure, because the user believes they are out."""
    client, _ = app
    _signup(client)
    first = _auth_login(client)
    derived = client.post("/auth/derive", json={"access_token": first["access_token"]}).json()

    r = client.post(
        "/auth/logout-all", headers={"Authorization": f"Bearer {first['access_token']}"}
    )
    assert r.status_code == 200
    dead = client.post("/auth/refresh", json={"refresh_token": derived["refresh_token"]})
    assert dead.status_code == 401


def test_a_forged_token_buys_nothing(app):
    client, _ = app
    r = client.post("/auth/derive", json={"access_token": "not.a.token"})
    assert r.status_code == 401


def test_an_absent_token_buys_nothing(app):
    client, _ = app
    assert client.post("/auth/derive", json={}).status_code == 401
