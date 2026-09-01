"""Contract tests for the independently packaged ``v2/accounts`` service.

This service owns identity, sessions, budgets and the spend ledger, and the daemon only ever
sees the HTTP contract — so these tests pin the CONTRACT (status codes + payload keys), not the
SQLite implementation behind it. That is what lets Stage 2 swap SQLite for Postgres safely.

Loaded by path (like tests/unit/test_model_proxy_service.py) because ``accounts/`` is a
standalone service directory, not an importable package. ``DB_PATH`` is resolved at module
import, so every test gets a FRESH module bound to its own tmp database — which also resets
the in-process rate-limiter dict between tests.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ACCOUNTS_APP = Path(__file__).resolve().parents[2] / "accounts" / "app.py"


def _load_app_module(monkeypatch, tmp_path, **env: str):
    """Import a pristine copy of accounts/app.py bound to a throwaway DB."""
    monkeypatch.setenv("AGENTD_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("AGENTD_AUTH_ISSUER", "https://accounts.test.invalid")
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "0/0")  # off unless a test asks for it
    monkeypatch.delenv("ACCOUNTS_INTERNAL_KEY", raising=False)
    monkeypatch.delenv("ACCOUNTS_SESSION_TTL_DAYS", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    spec = importlib.util.spec_from_file_location("agentd_accounts_app", ACCOUNTS_APP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def accounts(monkeypatch, tmp_path):
    """(client, module) with the schema created — TestClient as a context manager fires startup."""
    module = _load_app_module(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        yield client, module


def _signup(client, email="a@b.com", password="hunter2hunter2", budget=None) -> str:
    body: dict = {"email": email, "password": password}
    if budget is not None:
        body["budget_usd"] = budget
    r = client.post("/signup", json=body)
    assert r.status_code == 200, r.text
    return r.json()["account_id"]


def _login(client, email="a@b.com", password="hunter2hunter2") -> str:
    r = client.post("/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# --- health ------------------------------------------------------------------


def test_health_reports_the_service(accounts):
    client, _ = accounts
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "service": "accounts"}


# --- signup / login ----------------------------------------------------------


def test_signup_login_resolve_round_trip(accounts):
    client, _ = accounts
    account_id = _signup(client, budget=10)
    assert account_id.startswith("acct_")

    token = _login(client)
    # A signed access token, not the opaque `sess_` string this service used to mint. Asserted
    # by SHAPE rather than prefix: the point is that the credential is self-describing and
    # verifiable without asking us, which is what took this service off the model-call hot path.
    assert token.count(".") == 2 and not token.startswith("sess_")

    r = client.get("/resolve", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["account_id"] == account_id
    assert body["email"] == "a@b.com"
    assert body["budget_usd"] == 10
    assert body["spent_usd"] == 0
    assert body["over"] is False


def test_signup_normalises_email_case_and_whitespace(accounts):
    client, _ = accounts
    _signup(client, email="  MiXeD@Case.COM  ")
    # the lowercased form is what login must match
    assert _login(client, email="mixed@case.com")


@pytest.mark.parametrize(
    "email,password,detail",
    [
        ("not-an-email", "hunter2hunter2", "valid email required"),
        ("", "hunter2hunter2", "valid email required"),
        ("a@b.com", "short", "password must be at least 8 characters"),
    ],
)
def test_signup_rejects_bad_input(accounts, email, password, detail):
    client, _ = accounts
    r = client.post("/signup", json={"email": email, "password": password})
    assert r.status_code == 400
    assert r.json()["detail"] == detail


def test_signup_rejects_duplicate_email(accounts):
    client, _ = accounts
    _signup(client)
    r = client.post("/signup", json={"email": "a@b.com", "password": "hunter2hunter2"})
    assert r.status_code == 409


def test_login_rejects_wrong_password_and_unknown_email(accounts):
    client, _ = accounts
    _signup(client)
    assert client.post("/login", json={"email": "a@b.com", "password": "wrongwrongwrong"}).status_code == 401
    assert client.post("/login", json={"email": "nobody@b.com", "password": "hunter2hunter2"}).status_code == 401


# --- token handling ----------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic xyz"}, {"Authorization": "Bearer nope"}])
def test_resolve_rejects_missing_or_bogus_credentials(accounts, headers):
    client, _ = accounts
    _signup(client)
    assert client.get("/resolve", headers=headers).status_code == 401


def test_a_credential_this_service_did_not_mint_is_refused(monkeypatch, tmp_path):
    """Replaces two tests that pinned the old `sessions` table and its ACCOUNTS_SESSION_TTL_DAYS
    knob. Both are gone: a credential is now a signed token that carries its own expiry, so there
    is no server-side session row to purge and no TTL setting to honour.

    Access-token expiry is covered where it now lives — tests/unit/test_identity_auth.py and
    tests/unit/test_jwks_verifier.py. What is worth pinning HERE is the property those old tests
    were really protecting: a string that did not come from us gets nothing.
    """
    module = _load_app_module(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        _signup(client)
        for bogus in ("sess_looks_like_the_old_kind", "", "not-a-token", "a.b.c"):
            r = client.get("/resolve", headers={"Authorization": f"Bearer {bogus}"})
            assert r.status_code == 401, bogus


# --- the ledger: /usage is trusted-writers-only ------------------------------


def test_usage_is_open_when_no_internal_key_is_configured(accounts):
    """Local dev: no key set => everything is trusted (today's behaviour)."""
    client, _ = accounts
    account_id = _signup(client)
    r = client.post("/usage", json={"account_id": account_id, "cost_usd": 0.5})
    assert r.status_code == 200
    assert r.json()["spent_usd"] == 0.5


def test_usage_requires_the_internal_key_once_configured(monkeypatch, tmp_path):
    module = _load_app_module(monkeypatch, tmp_path, ACCOUNTS_INTERNAL_KEY="s3cret")
    with TestClient(module.app) as client:
        account_id = _signup(client)

        denied = client.post("/usage", json={"account_id": account_id, "cost_usd": 1})
        assert denied.status_code == 401

        wrong = client.post(
            "/usage", json={"account_id": account_id, "cost_usd": 1}, headers={"X-Internal-Key": "nope"}
        )
        assert wrong.status_code == 401

        ok = client.post(
            "/usage", json={"account_id": account_id, "cost_usd": 1}, headers={"X-Internal-Key": "s3cret"}
        )
        assert ok.status_code == 200
        assert ok.json()["spent_usd"] == 1


def test_usage_rejects_unknown_account_and_missing_id(accounts):
    client, _ = accounts
    assert client.post("/usage", json={"account_id": "acct_nope", "cost_usd": 1}).status_code == 404
    assert client.post("/usage", json={"cost_usd": 1}).status_code == 400


def test_usage_accumulates_and_trips_the_budget(accounts):
    client, _ = accounts
    account_id = _signup(client, budget=1.0)

    first = client.post(
        "/usage",
        json={"account_id": account_id, "model": "gemini", "in_tokens": 10, "out_tokens": 5, "cost_usd": 0.4},
    ).json()
    assert first["spent_usd"] == 0.4
    assert first["over"] is False
    assert first["remaining_usd"] == pytest.approx(0.6)

    second = client.post("/usage", json={"account_id": account_id, "cost_usd": 0.7}).json()
    assert second["spent_usd"] == pytest.approx(1.1)
    assert second["over"] is True
    assert second["remaining_usd"] == 0.0  # clamped, never negative


def test_null_budget_means_unlimited(accounts):
    client, _ = accounts
    account_id = _signup(client)  # no budget_usd => NULL => unlimited
    body = client.post("/usage", json={"account_id": account_id, "cost_usd": 9999}).json()
    assert body["over"] is False
    assert body["remaining_usd"] is None


# --- /budget authorization ---------------------------------------------------


def test_budget_readable_by_internal_key_and_by_the_owner(monkeypatch, tmp_path):
    module = _load_app_module(monkeypatch, tmp_path, ACCOUNTS_INTERNAL_KEY="s3cret")
    with TestClient(module.app) as client:
        mine = _signup(client, email="mine@b.com", budget=5)
        _signup(client, email="other@b.com", budget=5)
        my_token = _login(client, email="mine@b.com")
        other_token = _login(client, email="other@b.com")

        # trusted infra can read any account
        infra = client.get(f"/budget/{mine}", headers={"X-Internal-Key": "s3cret"})
        assert infra.status_code == 200
        assert infra.json()["budget_usd"] == 5

        # the owner can read their own
        own = client.get(f"/budget/{mine}", headers={"Authorization": f"Bearer {my_token}"})
        assert own.status_code == 200
        assert own.json()["account_id"] == mine

        # somebody else's token cannot
        assert client.get(f"/budget/{mine}", headers={"Authorization": f"Bearer {other_token}"}).status_code == 403

        # no credential at all
        assert client.get(f"/budget/{mine}").status_code == 401


def test_budget_view_shape_and_unknown_account(accounts):
    client, _ = accounts
    account_id = _signup(client, budget=2)
    body = client.get(f"/budget/{account_id}").json()
    assert set(body) == {"account_id", "budget_usd", "spent_usd", "remaining_usd", "over", "period"}
    assert body["period"].count("-") == 1  # 'YYYY-MM'

    assert client.get("/budget/acct_nope").status_code == 404


# --- rate limiting -----------------------------------------------------------


def test_rate_limit_trips_on_repeated_attempts(monkeypatch, tmp_path):
    module = _load_app_module(monkeypatch, tmp_path)
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "2/60")  # read per-call, so set it after import
    with TestClient(module.app) as client:
        assert client.post("/login", json={"email": "a@b.com", "password": "x"}).status_code == 401
        assert client.post("/login", json={"email": "a@b.com", "password": "x"}).status_code == 401
        # third attempt in the window is refused before credentials are even checked
        assert client.post("/login", json={"email": "a@b.com", "password": "x"}).status_code == 429


def test_rate_limit_window_resets(monkeypatch, tmp_path):
    module = _load_app_module(monkeypatch, tmp_path)
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "1/60")
    with TestClient(module.app) as client:
        assert client.post("/login", json={"email": "a@b.com", "password": "x"}).status_code == 401
        assert client.post("/login", json={"email": "a@b.com", "password": "x"}).status_code == 429

        real_now = module._now()
        monkeypatch.setattr(module, "_now", lambda: real_now + 61)
        assert client.post("/login", json={"email": "a@b.com", "password": "x"}).status_code == 401


def test_rate_limit_is_per_client_ip(monkeypatch, tmp_path):
    module = _load_app_module(monkeypatch, tmp_path)
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "1/60")
    with TestClient(module.app) as client:
        one = {"X-Forwarded-For": "10.0.0.1"}
        two = {"X-Forwarded-For": "10.0.0.2"}
        assert client.post("/login", json={"email": "a@b.com", "password": "x"}, headers=one).status_code == 401
        assert client.post("/login", json={"email": "a@b.com", "password": "x"}, headers=one).status_code == 429
        # a different IP has its own window
        assert client.post("/login", json={"email": "a@b.com", "password": "x"}, headers=two).status_code == 401


# --- schema migration --------------------------------------------------------
#
# Every test above starts from an empty tmp database, where CREATE TABLE builds `usage` with
# all of today's columns. Production does NOT: the file lives on EFS and outlives the image,
# so startup runs against a table some EARLIER build wrote. That gap shipped a crash loop once
# (an index over `agent_id` inside the schema script, evaluated before the ALTER TABLE that
# adds the column), so the upgrade path gets its own tests with an old database on disk.

# The `usage` table as it shipped BEFORE correlation ids, credits and per-agent attribution.
_ORIGINAL_SCHEMA = """
    CREATE TABLE accounts (
        id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw_salt TEXT NOT NULL,
        pw_hash TEXT NOT NULL, budget_usd REAL, active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL
    );
    CREATE TABLE sessions (
        token TEXT PRIMARY KEY, account_id TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE TABLE usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, ts REAL NOT NULL,
        month TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', in_tokens INTEGER NOT NULL DEFAULT 0,
        out_tokens INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0
    );
"""


def _seed_original_db(tmp_path, account_id="acct_old", cost=0.25, budget=None):
    """Write a database in the pre-migration schema, with a row worth preserving."""
    import sqlite3
    import time

    month = time.strftime("%Y-%m", time.gmtime())  # spend is read per CURRENT period
    path = tmp_path / "accounts.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_ORIGINAL_SCHEMA)
    conn.execute(
        "INSERT INTO accounts VALUES (?, 'old@b.com', 'salt', 'hash', ?, 1, 1.0)",
        (account_id, budget),
    )
    conn.execute(
        "INSERT INTO usage (account_id, ts, month, model, cost_usd) VALUES (?, 1.0, ?, 'm', ?)",
        (account_id, month, cost),
    )
    conn.commit()
    conn.close()
    return path


def test_startup_migrates_a_database_written_by_an_older_build(monkeypatch, tmp_path):
    """The regression: startup against an old `usage` table must not raise."""
    _seed_original_db(tmp_path)
    module = _load_app_module(monkeypatch, tmp_path)
    with TestClient(module.app) as client:  # fires startup -> _init_db
        assert client.get("/health").status_code == 200

    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "accounts.db"))
    columns = {r[1] for r in conn.execute("PRAGMA table_info(usage)")}
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(usage)")}
    conn.close()
    assert {"run_id", "turn_id", "credits", "funding_source", "agent_id", "model_tier", "cached_tokens"} <= columns
    # The indexes are the part that regressed: added columns must end up indexed too.
    assert {"ix_usage_agent", "ix_usage_run", "ix_usage_acct_month"} <= indexes


def test_migration_preserves_existing_rows(monkeypatch, tmp_path):
    """An upgrade is not allowed to lose the ledger it was upgrading."""
    _seed_original_db(tmp_path, account_id="acct_old", cost=0.25, budget=2)
    module = _load_app_module(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        r = client.get("/budget/acct_old")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["spent_usd"] == pytest.approx(0.25)
        assert body["remaining_usd"] == pytest.approx(1.75)


def test_startup_is_idempotent_across_restarts(monkeypatch, tmp_path):
    """ECS restarts the container; the second boot runs _init_db over its own output."""
    _seed_original_db(tmp_path)
    for _ in range(3):
        module = _load_app_module(monkeypatch, tmp_path)
        with TestClient(module.app) as client:
            assert client.get("/health").status_code == 200
