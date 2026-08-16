"""Contract tests for the /admin/* control plane.

THE SECURITY-CRITICAL HALF FIRST. These routes are the only ones in the platform that let one
signed-in human read and change ANOTHER account's money, access and keys, so the tests that matter
most are the ones that prove the door is shut: no token, a non-admin token, a demoted admin, and a
deployment that has configured nobody. Everything else here is contract-shape.

Loaded by path for the same reason test_accounts_service.py is — ``accounts/`` is a standalone
service directory, not an importable package — and each test gets a fresh module bound to its own
tmp database, which also resets the process-global settings read from the environment.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ACCOUNTS_APP = Path(__file__).resolve().parents[2] / "accounts" / "app.py"

PASSWORD = "hunter2hunter2"


def _load(monkeypatch, tmp_path, **env: str):
    monkeypatch.setenv("AGENTD_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("AGENTD_AUTH_ISSUER", "https://accounts.test.invalid")
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "0/0")
    monkeypatch.delenv("ACCOUNTS_INTERNAL_KEY", raising=False)
    # Every AWS-backed panel off by default: these tests must never reach a network or a profile.
    for name in (
        "AGENTD_ADMIN_IDENTITIES",
        "AGENTD_APP_SECRET_ID",
        "AGENTD_CREATORS_TABLE",
        "AGENTD_ECS_CLUSTER",
        "AGENTD_KEY_CONSUMERS",
        "AGENTD_REGISTRY",
        "AGENTD_PUBLISH_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    spec = importlib.util.spec_from_file_location("agentd_accounts_admin_test", ACCOUNTS_APP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _signup(client, email: str) -> str:
    r = client.post("/signup", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["account_id"]


def _token(client, email: str) -> str:
    r = client.post("/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def plain(monkeypatch, tmp_path):
    """A deployment with NO admins configured — the fail-closed baseline."""
    module = _load(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        yield client, module


@pytest.fixture
def stack(monkeypatch, tmp_path):
    """boss@example.com is a config admin; user@example.com is an ordinary account."""
    module = _load(monkeypatch, tmp_path, AGENTD_ADMIN_IDENTITIES="boss@example.com")
    with TestClient(module.app) as client:
        _signup(client, "boss@example.com")
        _signup(client, "user@example.com")
        yield client, module


# --- the door ----------------------------------------------------------------


def test_no_token_is_401_not_403(stack):
    """Told apart deliberately: 401 is fixed by signing in, 403 is not."""
    client, _ = stack
    assert client.get("/admin/accounts").status_code == 401


def test_garbage_token_is_401(stack):
    client, _ = stack
    assert client.get("/admin/accounts", headers=_auth("not-a-token")).status_code == 401


def test_ordinary_account_is_403(stack):
    client, _ = stack
    token = _token(client, "user@example.com")
    r = client.get("/admin/accounts", headers=_auth(token))
    assert r.status_code == 403
    # Says nothing about WHO is an admin — that is not information a non-admin is owed.
    assert "not a platform admin" in r.json()["detail"]


def test_empty_admin_list_refuses_everyone(plain):
    """A deployment that configured nobody must refuse admin calls, not accept them from anyone
    who happens to be signed in. Same fail-closed rule as the creator roster."""
    client, _ = plain
    _signup(client, "someone@example.com")
    token = _token(client, "someone@example.com")
    assert client.get("/admin/accounts", headers=_auth(token)).status_code == 403


def test_config_admin_passes(stack):
    client, _ = stack
    token = _token(client, "boss@example.com")
    assert client.get("/admin/accounts", headers=_auth(token)).status_code == 200


def test_whoami_answers_200_for_non_admins_too(stack):
    """The client needs "no" as DATA to decide whether to render the nav. An error here would be
    indistinguishable from the service being down, which would hide the nav from real admins."""
    client, _ = stack
    r = client.get("/admin/whoami", headers=_auth(_token(client, "user@example.com")))
    assert r.status_code == 200
    assert r.json()["is_admin"] is False
    assert r.json()["source"] == ""

    r = client.get("/admin/whoami", headers=_auth(_token(client, "boss@example.com")))
    assert r.json()["is_admin"] is True
    assert r.json()["source"] == "config"


# --- promotion and demotion --------------------------------------------------


def test_promote_then_demote_round_trip(stack):
    client, module = stack
    boss, user = _token(client, "boss@example.com"), _token(client, "user@example.com")
    user_id = client.get("/admin/whoami", headers=_auth(user)).json()["account_id"]

    assert client.get("/admin/accounts", headers=_auth(user)).status_code == 403

    r = client.post(
        f"/admin/accounts/{user_id}/admin", json={"is_admin": True}, headers=_auth(boss)
    )
    assert r.status_code == 200 and r.json()["is_admin"] is True

    # Takes effect on the NEXT call with the SAME token: the admin check reads the table, it is
    # not a claim baked into the credential.
    assert client.get("/admin/accounts", headers=_auth(user)).status_code == 200
    assert client.get("/admin/whoami", headers=_auth(user)).json()["source"] == "roster"

    assert (
        client.post(
            f"/admin/accounts/{user_id}/admin", json={"is_admin": False}, headers=_auth(boss)
        ).status_code
        == 200
    )
    assert client.get("/admin/accounts", headers=_auth(user)).status_code == 403


def test_config_admin_cannot_be_demoted_and_says_why(stack):
    """A silent no-op would leave the dashboard showing a change that did not happen."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    boss_id = client.get("/admin/whoami", headers=_auth(boss)).json()["account_id"]
    _signup(client, "other@example.com")
    other = _token(client, "other@example.com")
    other_id = client.get("/admin/whoami", headers=_auth(other)).json()["account_id"]
    client.post(f"/admin/accounts/{other_id}/admin", json={"is_admin": True}, headers=_auth(boss))

    r = client.post(
        f"/admin/accounts/{boss_id}/admin", json={"is_admin": False}, headers=_auth(other)
    )
    assert r.status_code == 409
    assert "deploy configuration" in r.json()["detail"]
    # And they are still an admin afterwards.
    assert client.get("/admin/whoami", headers=_auth(boss)).json()["is_admin"] is True


def test_cannot_demote_or_disable_yourself(stack):
    """Both lockouts are unrecoverable from inside the product: re-enabling requires signing in,
    and signing in requires the account to be enabled."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    boss_id = client.get("/admin/whoami", headers=_auth(boss)).json()["account_id"]

    r = client.post(
        f"/admin/accounts/{boss_id}/admin", json={"is_admin": False}, headers=_auth(boss)
    )
    assert r.status_code == 400
    r = client.post(
        f"/admin/accounts/{boss_id}/active", json={"active": False}, headers=_auth(boss)
    )
    assert r.status_code == 400


# --- accounts ----------------------------------------------------------------


def test_listing_clamps_the_page_size(stack):
    """Not defensive padding: this shares a process with /resolve, the model-call hot path."""
    client, module = stack
    boss = _token(client, "boss@example.com")
    r = client.get("/admin/accounts?limit=100000", headers=_auth(boss))
    assert r.status_code == 200
    assert r.json()["limit"] == module.admin_api.PAGE_MAX


def test_listing_searches_and_reports_admin_source(stack):
    client, _ = stack
    boss = _token(client, "boss@example.com")
    r = client.get("/admin/accounts?q=boss", headers=_auth(boss))
    body = r.json()
    assert body["total"] == 1
    assert body["accounts"][0]["email"] == "boss@example.com"
    assert body["accounts"][0]["admin_source"] == "config"


def test_set_budget_and_clear_it(stack):
    client, _ = stack
    boss = _token(client, "boss@example.com")
    user_id = client.get("/admin/whoami", headers=_auth(_token(client, "user@example.com"))).json()[
        "account_id"
    ]

    r = client.post(
        f"/admin/accounts/{user_id}/budget", json={"budget_usd": 25}, headers=_auth(boss)
    )
    assert r.status_code == 200 and r.json()["budget_usd"] == 25

    # null is a real value here: it means unlimited, not "unchanged".
    r = client.post(
        f"/admin/accounts/{user_id}/budget", json={"budget_usd": None}, headers=_auth(boss)
    )
    assert r.status_code == 200 and r.json()["budget_usd"] is None

    r = client.post(
        f"/admin/accounts/{user_id}/budget", json={"budget_usd": -1}, headers=_auth(boss)
    )
    assert r.status_code == 400


def test_disable_takes_effect_immediately_on_a_live_token(stack):
    """A disabled account must not ride out the remaining minutes of an already-issued token."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    user = _token(client, "user@example.com")
    user_id = client.get("/admin/whoami", headers=_auth(user)).json()["account_id"]
    assert client.get("/resolve", headers=_auth(user)).status_code == 200

    client.post(f"/admin/accounts/{user_id}/active", json={"active": False}, headers=_auth(boss))
    assert client.get("/resolve", headers=_auth(user)).status_code == 401

    client.post(f"/admin/accounts/{user_id}/active", json={"active": True}, headers=_auth(boss))
    assert client.get("/resolve", headers=_auth(user)).status_code == 200


def test_granting_credits_moves_the_balance_and_arms_enforcement(stack):
    """The side effect worth pinning: a first grant flips credits_enforced on permanently, which
    is what turns a zero balance from "free tier" into "refused"."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    user_id = client.get("/admin/whoami", headers=_auth(_token(client, "user@example.com"))).json()[
        "account_id"
    ]

    before = client.get(f"/admin/accounts/{user_id}", headers=_auth(boss)).json()
    assert before["credits_remaining"] == 0
    assert before["credits_enforced"] is False

    r = client.post(
        f"/admin/accounts/{user_id}/credits", json={"credits": 5000}, headers=_auth(boss)
    )
    assert r.status_code == 200

    after = client.get(f"/admin/accounts/{user_id}", headers=_auth(boss)).json()
    assert after["credits_remaining"] == 5000
    assert after["credits_enforced"] is True
    assert len(after["grants"]) == 1


def test_revoking_sessions_kills_refresh_but_reports_the_access_window(stack):
    """Access tokens already minted stay valid until they expire. Reported rather than hidden, so
    an admin knows to pair this with disabling the account when it must be instant."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    login = client.post(
        "/login", json={"email": "user@example.com", "password": PASSWORD}
    ).json()
    user_id = client.get("/admin/whoami", headers=_auth(login["access_token"])).json()["account_id"]

    r = client.post(f"/admin/accounts/{user_id}/sessions/revoke", headers=_auth(boss))
    assert r.status_code == 200
    assert r.json()["revoked"] >= 1
    assert r.json()["access_tokens_valid_for_s"] > 0

    refreshed = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert refreshed.status_code == 401


def test_unknown_account_is_404_everywhere(stack):
    client, _ = stack
    boss = _token(client, "boss@example.com")
    assert client.get("/admin/accounts/acct_nope", headers=_auth(boss)).status_code == 404
    assert (
        client.post(
            "/admin/accounts/acct_nope/budget", json={"budget_usd": 1}, headers=_auth(boss)
        ).status_code
        == 404
    )
    assert (
        client.post("/admin/accounts/acct_nope/sessions/revoke", headers=_auth(boss)).status_code
        == 404
    )


# --- usage and money ---------------------------------------------------------


def test_usage_rejects_an_unknown_grouping(stack):
    """group_by picks a COLUMN, and a column name cannot be a bound parameter — so it comes from
    a fixed map and anything else is refused rather than interpolated."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    assert client.get("/admin/usage?group_by=agent", headers=_auth(boss)).status_code == 200
    r = client.get("/admin/usage?group_by=1;DROP TABLE usage", headers=_auth(boss))
    assert r.status_code == 400
    # And the table is still there.
    assert client.get("/admin/usage", headers=_auth(boss)).status_code == 200


def test_usage_rolls_up_by_agent(stack):
    client, module = stack
    boss = _token(client, "boss@example.com")
    user_id = client.get("/admin/whoami", headers=_auth(_token(client, "user@example.com"))).json()[
        "account_id"
    ]
    client.post(
        "/usage",
        json={
            "account_id": user_id,
            "model": "gemini-3-pro",
            "agent_id": "figure-creator",
            "in_tokens": 100,
            "out_tokens": 50,
            "cost_usd": 0.01,
        },
    )
    rows = client.get("/admin/usage?group_by=agent", headers=_auth(boss)).json()["rows"]
    assert rows and rows[0]["key"] == "figure-creator"
    assert rows[0]["in_tokens"] == 100
    assert rows[0]["calls"] == 1


def test_overview_counts_accounts_and_admins(stack):
    client, _ = stack
    boss = _token(client, "boss@example.com")
    body = client.get("/admin/overview", headers=_auth(boss)).json()
    assert body["accounts_total"] == 2
    assert body["accounts_active"] == 2
    assert "top_agents" in body and "credits_outstanding" in body


def test_products_upsert_is_editable_not_duplicated(stack):
    """Editing a price must keep the same product, so existing subscriptions still point at it."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    payload = {"id": "pack_test", "kind": "credit_pack", "credits": 1000, "price_usd": 1.0}
    assert client.post("/admin/products", json=payload, headers=_auth(boss)).status_code == 200
    assert (
        client.post(
            "/admin/products", json={**payload, "price_usd": 2.0}, headers=_auth(boss)
        ).status_code
        == 200
    )
    products = client.get("/admin/products", headers=_auth(boss)).json()["products"]
    mine = [p for p in products if p["id"] == "pack_test"]
    assert len(mine) == 1 and mine[0]["price_usd"] == 2.0


def test_products_reject_an_unknown_kind(stack):
    client, _ = stack
    boss = _token(client, "boss@example.com")
    r = client.post(
        "/admin/products", json={"id": "x", "kind": "free_money"}, headers=_auth(boss)
    )
    assert r.status_code == 400


def test_entitlement_grant_and_remove(stack):
    client, _ = stack
    boss = _token(client, "boss@example.com")
    user_id = client.get("/admin/whoami", headers=_auth(_token(client, "user@example.com"))).json()[
        "account_id"
    ]
    client.post(
        f"/admin/accounts/{user_id}/entitlements",
        json={"agent_id": "figure-creator", "granted": True},
        headers=_auth(boss),
    )
    detail = client.get(f"/admin/accounts/{user_id}", headers=_auth(boss)).json()
    assert [e["agent_id"] for e in detail["entitlements"]] == ["figure-creator"]

    client.post(
        f"/admin/accounts/{user_id}/entitlements",
        json={"agent_id": "figure-creator", "granted": False},
        headers=_auth(boss),
    )
    detail = client.get(f"/admin/accounts/{user_id}", headers=_auth(boss)).json()
    assert detail["entitlements"] == []


def test_ledger_reports_the_balance_check(stack):
    """`balanced` false means a posting bypassed ledger.post() — a correctness bug in the books,
    so it is surfaced rather than hidden behind a pretty number."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    body = client.get("/admin/ledger", headers=_auth(boss)).json()
    assert body["balanced"] is True
    assert "accounts" in body and "entries" in body


# --- keys and catalog, unconfigured ------------------------------------------


def test_keys_reports_unconfigured_sources_rather_than_failing(stack):
    """A laptop has no AWS at all, and the right answer there is an honest gap in the panel — not
    a 500, and not a page that implies the keys are fine."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    body = client.get("/admin/keys", headers=_auth(boss)).json()
    assert body["secrets"]["configured"] is False
    assert body["creator_keys"]["configured"] is False
    # The signing key IS local, so it is always real.
    assert len(body["signing_keys"]) >= 1
    assert any(k["active"] for k in body["signing_keys"])


def test_rotating_the_signing_key_keeps_the_old_one_verifiable(stack):
    """Nobody is signed out: the outgoing key stays in JWKS while the new one signs."""
    client, _ = stack
    boss = _token(client, "boss@example.com")
    user = _token(client, "user@example.com")
    before = client.get("/auth/jwks.json").json()["keys"]

    r = client.post("/admin/keys/signing/rotate", headers=_auth(boss))
    assert r.status_code == 200
    assert r.json()["previous_key_valid_for_s"] > 0

    after = client.get("/auth/jwks.json").json()["keys"]
    assert len(after) == len(before) + 1
    # A token minted before the rotation still resolves.
    assert client.get("/resolve", headers=_auth(user)).status_code == 200


def test_agents_reports_an_unconfigured_registry(stack):
    client, _ = stack
    boss = _token(client, "boss@example.com")
    body = client.get("/admin/agents", headers=_auth(boss)).json()
    assert body["configured"] is False
    assert body["bundles"] == []


def test_agents_normalises_the_engine_row_to_a_list(monkeypatch, tmp_path):
    """The index carries `engine` as a LIST (one row per platform) and carried a bare object in an
    earlier schema. Reading it as an object when it is a list does not crash — it silently renders
    nothing, which is the worst of the three outcomes and the reason this is normalised server-side.
    Caught against the real dev registry, not in review."""
    module = _load(monkeypatch, tmp_path, AGENTD_ADMIN_IDENTITIES="boss@example.com",
                   AGENTD_REGISTRY="https://registry.test.invalid/index.json")
    listed = {"schema": 2, "bundles": [], "engine": [{"platform": "win", "version": "0.1.8"}]}
    objected = {"schema": 1, "bundles": [], "engine": {"platform": "win", "version": "0.0.9"}}

    with TestClient(module.app) as client:
        _signup(client, "boss@example.com")
        boss = _token(client, "boss@example.com")
        for index, expected in ((listed, "0.1.8"), (objected, "0.0.9"), ({"bundles": []}, None)):
            monkeypatch.setattr(module.admin_api, "_fetch_json", lambda _u, i=index: i)
            body = client.get("/admin/agents", headers=_auth(boss)).json()
            assert isinstance(body["engines"], list)
            assert (body["engines"][0]["version"] if body["engines"] else None) == expected


def test_creators_without_a_publish_service_is_503_not_a_crash(stack):
    client, _ = stack
    boss = _token(client, "boss@example.com")
    r = client.get("/admin/creators", headers=_auth(boss))
    assert r.status_code == 503
    assert "publish service" in r.json()["detail"]


def test_creators_says_so_when_the_publish_service_can_only_answer_pending(monkeypatch, tmp_path):
    """THE WRONG ANSWER PRESENTED CONFIDENTLY. An older publish image has /pending but not the full
    listing, and a registry whose creators are all already admitted has an EMPTY pending — so a
    silent fallback renders "no creators" on a marketplace that has several. Caught on the live dev
    stack, where exactly that happened."""
    module = _load(monkeypatch, tmp_path, AGENTD_ADMIN_IDENTITIES="boss@example.com",
                   AGENTD_PUBLISH_URL="http://publish.test.invalid")
    calls = []

    def fake_proxy(method, url, token, body=None):
        calls.append(url)
        if url.endswith("/creators"):
            return 404, {"message": "no route"}
        return 200, {"pending": [{"creator_id": "c-new", "name": "New"}]}

    monkeypatch.setattr(module.admin_api, "_proxy", fake_proxy)
    with TestClient(module.app) as client:
        _signup(client, "boss@example.com")
        r = client.get("/admin/creators", headers=_auth(_token(client, "boss@example.com")))
        assert r.status_code == 200
        body = r.json()
        assert body["partial"] is True
        assert "awaiting review" in body["reason"].lower()
        # And the degraded rows still carry a state, so the client renders ONE shape either way.
        assert body["creators"][0]["state"] == "pending_review"
    assert any(u.endswith("/creators") for u in calls) and any(u.endswith("/pending") for u in calls)


def test_setting_a_secret_without_a_secret_configured_is_503(stack):
    client, _ = stack
    boss = _token(client, "boss@example.com")
    r = client.post(
        "/admin/keys/secret", json={"name": "GEMINI_API_KEY", "value": "x"}, headers=_auth(boss)
    )
    assert r.status_code == 503


def test_setting_a_secret_requires_a_non_empty_value(monkeypatch, tmp_path):
    """Refused BEFORE any AWS call: an empty provider key would be written happily and would then
    fail every model call with an auth error from the provider instead of from us."""
    module = _load(
        monkeypatch,
        tmp_path,
        AGENTD_ADMIN_IDENTITIES="boss@example.com",
        AGENTD_APP_SECRET_ID="agentd/test/app",
    )
    with TestClient(module.app) as client:
        _signup(client, "boss@example.com")
        boss = _token(client, "boss@example.com")
        r = client.post(
            "/admin/keys/secret", json={"name": "GEMINI_API_KEY", "value": ""}, headers=_auth(boss)
        )
        assert r.status_code == 400


# --- the answer other services depend on -------------------------------------


def test_whoami_is_the_authority_other_services_ask(stack):
    """The publish Lambda decides "may this token admit a creator?" by calling THIS route with the
    token it already holds, rather than keeping its own copy of the admin list. Pinned as a
    contract because a change to these two field names silently breaks creator admission — the
    Lambda would read a missing `is_admin` as false and refuse every admin.
    """
    client, _ = stack
    body = client.get("/admin/whoami", headers=_auth(_token(client, "boss@example.com"))).json()
    assert set(body) >= {"account_id", "email", "is_admin", "source"}
    assert body["is_admin"] is True

    # And an invalid token is 401 here, which is what lets the Lambda tell "not signed in" from
    # "signed in and not an admin" without a second call.
    assert client.get("/admin/whoami", headers=_auth("garbage")).status_code == 401
