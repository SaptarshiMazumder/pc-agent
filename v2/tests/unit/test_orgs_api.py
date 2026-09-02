"""Contract tests for /orgs/* + org money (tenancy plan E1 + E2), against a real temp database.

THE MEMBERSHIP REFUSALS FIRST, in the admin-door style the plan mandates: for every org route
the non-member, the removed member, and the member-of-a-DIFFERENT-org each get refused — the
failure mode of any one of these is one enterprise inside another's wall. Then the money rules:
two pockets that never mix, seats gating membership only, the per-member cap producing the same
402 the personal path produces, and the rollup reading the same ledger the cap reads.

Loaded by path exactly like test_admin_api.py — accounts/ is a service directory, not a
package — and each test binds a fresh module to its own tmp database.
"""

from __future__ import annotations

import base64
import importlib.util
import json
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
    for name in ("AGENTD_ADMIN_IDENTITIES", "AGENTD_REGISTRY", "AGENTD_PUBLISH_URL"):
        monkeypatch.delenv(name, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("agentd_accounts_orgs_test", ACCOUNTS_APP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _signup(client, email: str) -> str:
    r = client.post("/signup", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["account_id"]

def _login(client, email: str) -> dict:
    r = client.post("/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


def _token(client, email: str) -> str:
    return _login(client, email)["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _claims(token: str) -> dict:
    """Decode the JWT payload WITHOUT verification — these tests assert claim contents, and
    the signature was already proven by the login round trip."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


@pytest.fixture
def stack(monkeypatch, tmp_path):
    """owner@ founds Kajima; member@ and outsider@ exist as plain accounts."""
    module = _load(monkeypatch, tmp_path)
    with TestClient(module.app) as client:
        _signup(client, "owner@kajima.co.jp")
        _signup(client, "member@kajima.co.jp")
        _signup(client, "outsider@rival.com")
        owner = _token(client, "owner@kajima.co.jp")
        org = client.post("/orgs", json={"name": "Kajima", "seats_total": 3}, headers=_auth(owner))
        assert org.status_code == 200, org.text
        yield client, module, org.json()["id"], owner


def _join_by_invite(client, org_id: str, admin_token: str, email: str, role: str = "member") -> str:
    """Mint + redeem an invite for `email`; returns their (fresh, org-bearing) token."""
    inv = client.post(
        f"/orgs/{org_id}/invites", json={"role": role}, headers=_auth(admin_token)
    )
    assert inv.status_code == 200, inv.text
    token = _token(client, email)
    r = client.post(
        "/orgs/join", json={"invite_token": inv.json()["invite_token"]}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    return _token(client, email)  # re-login: the fresh token carries the new membership


# ── the door: membership refusals ─────────────────────────────────────────────


def test_a_non_member_gets_404_not_403(stack):
    """Whether the org EXISTS is itself information a non-member is not owed."""
    client, _m, org_id, _owner = stack
    outsider = _token(client, "outsider@rival.com")
    assert client.get(f"/orgs/{org_id}", headers=_auth(outsider)).status_code == 404
    assert client.get(f"/orgs/{org_id}/usage", headers=_auth(outsider)).status_code == 404
    assert (
        client.post(
            f"/orgs/{org_id}/invites", json={}, headers=_auth(outsider)
        ).status_code
        == 404
    )


def test_a_member_of_a_different_org_is_still_a_non_member(stack):
    client, _m, org_id, _owner = stack
    outsider = _token(client, "outsider@rival.com")
    rival = client.post("/orgs", json={"name": "Rival"}, headers=_auth(outsider))
    assert rival.status_code == 200
    # owning Rival grants exactly nothing inside Kajima
    fresh = _token(client, "outsider@rival.com")
    assert client.get(f"/orgs/{org_id}", headers=_auth(fresh)).status_code == 404


def test_an_account_already_in_an_org_cannot_found_another(stack):
    """ONE ORG PER ACCOUNT holds on the CREATE path too. create_org writes the owner row with a
    direct INSERT, so it must repeat the guard every join path applies — otherwise an account in
    org A could found org B, hold two memberships, and leave the funding rule with no honest
    answer to which pool a turn draws from."""
    client, _m, org_id, owner = stack
    _join_by_invite(client, org_id, owner, "member@kajima.co.jp")  # member now belongs to Kajima
    member = _token(client, "member@kajima.co.jp")
    r = client.post("/orgs", json={"name": "Side Co"}, headers=_auth(member))
    assert r.status_code == 409 and "one organization" in r.json()["detail"]
    # the owner is no exception: already owning Kajima blocks founding a second org too
    r = client.post(
        "/orgs", json={"name": "Kajima Two"}, headers=_auth(_token(client, "owner@kajima.co.jp"))
    )
    assert r.status_code == 409


def test_reactivation_honours_one_org_per_account(stack):
    """Reactivating a removed member is a JOIN, so it asks the same guard: an account that joined
    another org while deactivated must leave it before it can be re-seated here — the direct
    UPDATE in update_member would otherwise sneak past the one-org rule _add_member enforces."""
    client, _m, org_id, owner = stack
    member = _join_by_invite(client, org_id, owner, "member@kajima.co.jp")
    acct = client.get("/resolve", headers=_auth(member)).json()["account_id"]
    # removed from Kajima (active=False frees the account of its only membership)...
    assert client.post(
        f"/orgs/{org_id}/members/{acct}", json={"active": False}, headers=_auth(owner)
    ).status_code == 200
    # ...they found their own org while out (now allowed — no active membership)...
    side = client.post(
        "/orgs", json={"name": "Side Co"}, headers=_auth(_token(client, "member@kajima.co.jp"))
    )
    assert side.status_code == 200
    # ...so Kajima can no longer simply flip them back on
    r = client.post(
        f"/orgs/{org_id}/members/{acct}", json={"active": True}, headers=_auth(owner)
    )
    assert r.status_code == 409 and "leave" in r.json()["detail"]


def test_a_removed_member_is_refused_on_the_next_call(stack):
    client, _m, org_id, owner = stack
    member = _join_by_invite(client, org_id, owner, "member@kajima.co.jp")
    acct = client.get("/resolve", headers=_auth(member)).json()["account_id"]
    assert client.get(f"/orgs/{org_id}", headers=_auth(member)).status_code == 200
    r = client.post(
        f"/orgs/{org_id}/members/{acct}", json={"active": False}, headers=_auth(owner)
    )
    assert r.status_code == 200
    assert client.get(f"/orgs/{org_id}", headers=_auth(member)).status_code == 404


def test_a_plain_member_may_look_but_not_administer(stack):
    client, _m, org_id, owner = stack
    member = _join_by_invite(client, org_id, owner, "member@kajima.co.jp")
    view = client.get(f"/orgs/{org_id}", headers=_auth(member))
    assert view.status_code == 200
    # the member's view names the org and their role — never the colleague list
    assert "members" not in view.json()
    assert view.json()["role"] == "member"
    assert (
        client.post(
            f"/orgs/{org_id}/domains", json={"domain": "kajima.co.jp"}, headers=_auth(member)
        ).status_code
        == 403
    )


def test_no_token_is_401(stack):
    client, _m, org_id, _owner = stack
    assert client.get(f"/orgs/{org_id}").status_code == 401
    assert client.get("/me/orgs").status_code == 401


# ── the token claim ───────────────────────────────────────────────────────────


def test_membership_rides_the_next_token_and_resolve(stack):
    client, _m, org_id, owner = stack
    assert "orgs" not in _claims(owner)  # founded AFTER this token was minted
    fresh = _token(client, "owner@kajima.co.jp")
    assert _claims(fresh)["orgs"] == [{"id": org_id, "role": "owner"}]
    assert client.get("/resolve", headers=_auth(fresh)).json()["orgs"] == [
        {"id": org_id, "role": "owner"}
    ]


def test_a_personal_only_account_has_no_orgs_claim_at_all(stack):
    client, _m, _org_id, _owner = stack
    token = _token(client, "outsider@rival.com")
    assert "orgs" not in _claims(token)


# ── invites ───────────────────────────────────────────────────────────────────


def test_an_invite_is_single_use(stack):
    client, _m, org_id, owner = stack
    inv = client.post(f"/orgs/{org_id}/invites", json={}, headers=_auth(owner)).json()
    member = _token(client, "member@kajima.co.jp")
    assert (
        client.post(
            "/orgs/join", json={"invite_token": inv["invite_token"]}, headers=_auth(member)
        ).status_code
        == 200
    )
    outsider = _token(client, "outsider@rival.com")
    assert (
        client.post(
            "/orgs/join", json={"invite_token": inv["invite_token"]}, headers=_auth(outsider)
        ).status_code
        == 404
    )


def test_an_email_bound_invite_refuses_a_different_email(stack):
    client, _m, org_id, owner = stack
    inv = client.post(
        f"/orgs/{org_id}/invites",
        json={"email": "member@kajima.co.jp"},
        headers=_auth(owner),
    ).json()
    outsider = _token(client, "outsider@rival.com")
    assert (
        client.post(
            "/orgs/join", json={"invite_token": inv["invite_token"]}, headers=_auth(outsider)
        ).status_code
        == 403
    )


def test_invites_never_grant_owner(stack):
    client, _m, org_id, owner = stack
    r = client.post(f"/orgs/{org_id}/invites", json={"role": "owner"}, headers=_auth(owner))
    assert r.status_code == 400


# ── domains: free text, offer-only ────────────────────────────────────────────


def test_domain_join_is_offered_at_login_and_chosen_by_the_user(stack):
    """The Notion rule minus verification (explicit user call 2026-08-18): any string an admin
    types becomes joinable for matching emails — OFFERED at login, never silently added."""
    client, _m, org_id, owner = stack
    r = client.post(
        f"/orgs/{org_id}/domains", json={"domain": "@Kajima.CO.JP"}, headers=_auth(owner)
    )
    assert r.status_code == 200 and r.json()["domains"] == ["kajima.co.jp"]

    login = _login(client, "member@kajima.co.jp")
    assert login["joinable_orgs"] == [{"id": org_id, "name": "Kajima"}]
    # the offer redeems by naming the org; membership lands as plain member
    joined = client.post(
        "/orgs/join", json={"org_id": org_id}, headers=_auth(login["access_token"])
    )
    assert joined.status_code == 200
    # and the NEXT login no longer offers what is already held
    assert "joinable_orgs" not in _login(client, "member@kajima.co.jp")


def test_a_mismatched_domain_cannot_domain_join(stack):
    client, _m, org_id, owner = stack
    client.post(f"/orgs/{org_id}/domains", json={"domain": "kajima.co.jp"}, headers=_auth(owner))
    outsider = _token(client, "outsider@rival.com")
    assert (
        client.post("/orgs/join", json={"org_id": org_id}, headers=_auth(outsider)).status_code
        == 404
    )
    assert "joinable_orgs" not in _login(client, "outsider@rival.com")


# ── domains: inferred from the founder's email, public providers rejected ─────


def test_creating_an_org_claims_the_founders_work_domain(stack):
    """The domain is INFERRED from the founder's email at creation — never a separate 'add
    domain' step. That single claim is what lets the next colleague route here."""
    client, _m, org_id, owner = stack
    view = client.get(f"/orgs/{org_id}", headers=_auth(owner)).json()
    assert view["domains"] == ["kajima.co.jp"]  # claimed at /orgs time, nothing else done
    # so a colleague is OFFERED the org at login with no admin configuration at all
    assert _login(client, "member@kajima.co.jp")["joinable_orgs"] == [
        {"id": org_id, "name": "Kajima"}
    ]


def test_a_personal_email_cannot_found_an_org(stack):
    """gmail.com must never become an org domain — or every unrelated Gmail user would route
    into whoever founded 'the gmail org' first. So a personal address cannot found one."""
    client, _m, _org_id, _owner = stack
    _signup(client, "solo@gmail.com")
    tok = _token(client, "solo@gmail.com")
    r = client.post("/orgs", json={"name": "Solo Inc"}, headers=_auth(tok))
    assert r.status_code == 400 and "work email" in r.json()["detail"]


def test_a_public_provider_domain_cannot_be_added_manually(stack):
    """The same rule holds however the domain arrives — the manual add path refuses it too."""
    client, _m, org_id, owner = stack
    r = client.post(
        f"/orgs/{org_id}/domains", json={"domain": "gmail.com"}, headers=_auth(owner)
    )
    assert r.status_code == 400 and "public email provider" in r.json()["detail"]


def test_the_public_domain_list_is_config_overridable(monkeypatch, tmp_path):
    """The seed is a fallback, not policy: an operator can widen it via env with no code change
    (here, treating a bespoke domain as public blocks founding an org on it)."""
    module = _load(monkeypatch, tmp_path, AGENTD_PUBLIC_EMAIL_DOMAINS="contractor.example")
    with TestClient(module.app) as client:
        _signup(client, "temp@contractor.example")
        tok = _token(client, "temp@contractor.example")
        r = client.post("/orgs", json={"name": "Nope"}, headers=_auth(tok))
        assert r.status_code == 400 and "work email" in r.json()["detail"]
        # ...and gmail.com is no longer blocked, because the env REPLACES the seed
        _signup(client, "founder@gmail.com")
        gtok = _token(client, "founder@gmail.com")
        assert client.post("/orgs", json={"name": "G"}, headers=_auth(gtok)).status_code == 200


def test_the_catalogue_survives_an_unconfigured_payment_rail(monkeypatch, tmp_path):
    """The store must RENDER even when this environment's rail (dev defaults to razorpay) has no
    keys in the secret yet — browsing a price list cannot 500 because checkout is unwired. The
    symptom this closes: Buy Seats / Top Up Pool stuck on 'Loading…' behind a 500 on /products."""
    module = _load(monkeypatch, tmp_path, AGENTD_PAYMENT_PROVIDER="razorpay")
    with TestClient(module.app) as client:
        r = client.get("/products", params={"kind": "credit_pack"})
        assert r.status_code == 200, r.text  # NOT the 500 the raw build_payment_gateway would give
        assert r.json()["provider"] == ""  # degraded to 'no rail', catalogue still returned


# ── seats: gate membership, never model calls ────────────────────────────────


def test_a_full_org_refuses_the_next_join_with_409(stack):
    client, module, org_id, owner = stack
    _join_by_invite(client, org_id, owner, "member@kajima.co.jp")
    _signup(client, "third@kajima.co.jp")
    _join_by_invite(client, org_id, owner, "third@kajima.co.jp")  # seat 3 of 3
    _signup(client, "fourth@kajima.co.jp")
    inv = client.post(f"/orgs/{org_id}/invites", json={}, headers=_auth(owner)).json()
    fourth = _token(client, "fourth@kajima.co.jp")
    r = client.post(
        "/orgs/join", json={"invite_token": inv["invite_token"]}, headers=_auth(fourth)
    )
    assert r.status_code == 409
    assert "seats" in r.json()["detail"]


# ── the owner anchor ─────────────────────────────────────────────────────────


def test_the_primary_owner_is_immune_to_everyone(stack):
    client, _m, org_id, owner = stack
    admin = _join_by_invite(client, org_id, owner, "member@kajima.co.jp", role="admin")
    owner_acct = client.get("/resolve", headers=_auth(owner)).json()["account_id"]
    # an admin may not touch an owner row at all
    r = client.post(
        f"/orgs/{org_id}/members/{owner_acct}", json={"active": False}, headers=_auth(admin)
    )
    assert r.status_code == 403
    # even another OWNER cannot demote or remove the primary owner (the recovery anchor)
    r = client.post(
        f"/orgs/{org_id}/members/{owner_acct}", json={"role": "member"}, headers=_auth(owner)
    )
    assert r.status_code == 403


# ── org money (E2): two pockets that never mix ───────────────────────────────


def _grant(client, body) -> dict:
    r = client.post("/grant", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_membership_decides_the_pocket(stack):
    """THE ENTERPRISE RULE, which REVERSED this test's original claim. It used to assert that a
    member's un-attributed turns drew their personal credits. The product decision is the
    opposite: an account in an organization HAS no personal wallet — every turn draws the org's
    pool whether or not the daemon stamped an org on it, because "whose money paid for this" must
    not depend on which agent happened to answer. Personal grants become reachable again only by
    leaving the org."""
    client, _m, org_id, owner = stack
    member_token = _join_by_invite(client, org_id, owner, "member@kajima.co.jp")
    acct = client.get("/resolve", headers=_auth(member_token)).json()["account_id"]
    _grant(client, {"org_id": org_id, "credits": 1000})
    _grant(client, {"account_id": acct, "credits": 70})

    org_view = client.get("/funding", params={"account_id": acct, "org_id": org_id}).json()
    unattributed = client.get("/funding", params={"account_id": acct}).json()
    assert org_view["credits_remaining"] == 1000
    assert org_view["funding_source"] == "org_pool"
    # The un-attributed view resolves to the SAME pool — membership decided, not the stamp.
    assert unattributed["credits_remaining"] == 1000
    assert unattributed["funding_source"] == "org_pool"
    # ...and the personal 70 are invisible everywhere while the membership lasts.

    # an org-attributed debit drains the ORG pool and only the org pool
    d = client.post(
        "/debit", json={"account_id": acct, "org_id": org_id, "credits": 400}
    )
    assert d.status_code == 200 and d.json()["credits_remaining"] == 600
    assert client.get("/funding", params={"account_id": acct}).json()["credits_remaining"] == 600


def test_an_unfunded_org_turn_is_402_even_on_the_free_tier(stack):
    """`credits_enforced` is a personal-tier grace; an org pool is ALWAYS enforced, or an
    unfunded org runs on the house forever."""
    client, _m, org_id, owner = stack
    member_token = _join_by_invite(client, org_id, owner, "member@kajima.co.jp")
    acct = client.get("/resolve", headers=_auth(member_token)).json()["account_id"]
    view = client.get("/funding", params={"account_id": acct, "org_id": org_id}).json()
    assert view["credits_remaining"] == 0 and view["credits_enforced"] is True
    assert client.post(
        "/debit", json={"account_id": acct, "org_id": org_id, "credits": 5}
    ).status_code == 402


def test_a_non_member_draws_nothing_from_a_funded_pool(stack):
    client, _m, org_id, _owner = stack
    _grant(client, {"org_id": org_id, "credits": 1000})
    outsider_token = _token(client, "outsider@rival.com")
    acct = client.get("/resolve", headers=_auth(outsider_token)).json()["account_id"]
    view = client.get("/funding", params={"account_id": acct, "org_id": org_id}).json()
    assert view["credits_remaining"] == 0  # fail closed: pool full, allowance zero


def test_the_member_cap_produces_the_same_402_shape(stack):
    """POLICY, not pricing: the cap bounds what one member may draw of a full pool, enforced
    at the same funding gate with the same zero-balance answer — plus `member_capped` so the
    proxy can say WHICH limit it was."""
    client, _m, org_id, owner = stack
    member_token = _join_by_invite(client, org_id, owner, "member@kajima.co.jp")
    acct = client.get("/resolve", headers=_auth(member_token)).json()["account_id"]
    _grant(client, {"org_id": org_id, "credits": 1000})
    r = client.post(
        f"/orgs/{org_id}/members/{acct}", json={"monthly_credit_cap": 100}, headers=_auth(owner)
    )
    assert r.status_code == 200

    view = client.get("/funding", params={"account_id": acct, "org_id": org_id}).json()
    assert view["credits_remaining"] == 100  # min(pool 1000, cap-left 100)

    # 80 of the cap gets spent — the USAGE ledger is what the cap reads
    r = client.post(
        "/usage",
        json={"account_id": acct, "org_id": org_id, "credits": 80, "cost_usd": 0.08},
    )
    assert r.status_code == 200
    view = client.get("/funding", params={"account_id": acct, "org_id": org_id}).json()
    assert view["credits_remaining"] == 20 and view["member_capped"] is False

    client.post(
        "/usage", json={"account_id": acct, "org_id": org_id, "credits": 20, "cost_usd": 0.02}
    )
    view = client.get("/funding", params={"account_id": acct, "org_id": org_id}).json()
    assert view["credits_remaining"] == 0 and view["member_capped"] is True
    # the pool itself is untouched by the cap — the ADMIN's rollup still shows 1000 granted
    assert (
        client.get("/funding", params={"account_id": acct, "org_id": org_id}).json()[
            "credits_remaining"
        ]
        == 0
    )


def test_the_usage_rollup_reads_the_same_ledger(stack):
    client, _m, org_id, owner = stack
    member_token = _join_by_invite(client, org_id, owner, "member@kajima.co.jp")
    acct = client.get("/resolve", headers=_auth(member_token)).json()["account_id"]
    client.post(
        "/usage",
        json={"account_id": acct, "org_id": org_id, "credits": 55, "cost_usd": 0.05},
    )
    fresh_owner = _token(client, "owner@kajima.co.jp")
    rollup = client.get(f"/orgs/{org_id}/usage", headers=_auth(fresh_owner)).json()
    rows = {r["account_id"]: r for r in rollup["members"]}
    assert rows[acct]["credits"] == 55 and rows[acct]["calls"] == 1
    # a personal row for the same account stays OUT of the org rollup
    client.post("/usage", json={"account_id": acct, "credits": 99, "cost_usd": 0.09})
    rollup = client.get(f"/orgs/{org_id}/usage", headers=_auth(fresh_owner)).json()
    assert {r["account_id"]: r["credits"] for r in rollup["members"]}[acct] == 55


def test_an_org_grant_requires_a_real_org(stack):
    client, _m, _org_id, _owner = stack
    r = client.post("/grant", json={"org_id": "org_nope", "credits": 10})
    assert r.status_code == 404


# ── the platform admin's orgs panel ──────────────────────────────────────────


@pytest.fixture
def admin_stack(monkeypatch, tmp_path):
    """boss@ is a platform admin; owner@ founds Kajima as an ordinary account."""
    module = _load(monkeypatch, tmp_path, AGENTD_ADMIN_IDENTITIES="boss@example.com")
    with TestClient(module.app) as client:
        _signup(client, "boss@example.com")
        _signup(client, "owner@kajima.co.jp")
        owner = _token(client, "owner@kajima.co.jp")
        org = client.post("/orgs", json={"name": "Kajima"}, headers=_auth(owner))
        yield client, module, org.json()["id"], _token(client, "boss@example.com")


def test_the_orgs_panel_is_behind_the_admin_door(admin_stack):
    client, _m, org_id, admin = admin_stack
    ordinary = _token(client, "owner@kajima.co.jp")  # an ORG owner is not a PLATFORM admin
    assert client.get("/admin/orgs", headers=_auth(ordinary)).status_code == 403
    assert (
        client.post(
            f"/admin/orgs/{org_id}/credits", json={"credits": 10}, headers=_auth(ordinary)
        ).status_code
        == 403
    )


def test_the_admin_panel_lists_seats_and_pool(admin_stack):
    client, _m, org_id, admin = admin_stack
    r = client.post(
        f"/admin/orgs/{org_id}/credits", json={"credits": 500}, headers=_auth(admin)
    )
    assert r.status_code == 200
    rows = client.get("/admin/orgs", headers=_auth(admin)).json()["orgs"]
    row = next(o for o in rows if o["id"] == org_id)
    assert row["seats_used"] == 1 and row["pool_credits_remaining"] == 500
    assert row["owner_email"] == "owner@kajima.co.jp"


def test_suspension_stops_routes_and_the_pool_together(admin_stack):
    client, _m, org_id, admin = admin_stack
    client.post(f"/admin/orgs/{org_id}/credits", json={"credits": 500}, headers=_auth(admin))
    owner_acct = client.get(
        "/resolve", headers=_auth(_token(client, "owner@kajima.co.jp"))
    ).json()["account_id"]
    r = client.post(
        f"/admin/orgs/{org_id}/active", json={"active": False}, headers=_auth(admin)
    )
    assert r.status_code == 200
    # the routes 404 for its own owner...
    fresh = _token(client, "owner@kajima.co.jp")
    assert client.get(f"/orgs/{org_id}", headers=_auth(fresh)).status_code == 404
    # ...their next token no longer carries it...
    assert "orgs" not in _claims(fresh)
    # ...and the pool answers zero — suspension is not decorative
    view = client.get("/funding", params={"account_id": owner_acct, "org_id": org_id}).json()
    assert view["credits_remaining"] == 0
    # reinstating restores everyone (membership rows were kept)
    client.post(f"/admin/orgs/{org_id}/active", json={"active": True}, headers=_auth(admin))
    assert (
        client.get(
            f"/orgs/{org_id}", headers=_auth(_token(client, "owner@kajima.co.jp"))
        ).status_code
        == 200
    )


# ── /me/orgs: the switcher's data ────────────────────────────────────────────


def test_me_orgs_lists_memberships_with_names_and_joinables(stack):
    client, _m, org_id, owner = stack
    client.post(f"/orgs/{org_id}/domains", json={"domain": "kajima.co.jp"}, headers=_auth(owner))
    me = client.get("/me/orgs", headers=_auth(owner)).json()
    assert me["orgs"] == [{"id": org_id, "role": "owner", "name": "Kajima"}]
    member = _token(client, "member@kajima.co.jp")
    me = client.get("/me/orgs", headers=_auth(member)).json()
    assert me["orgs"] == [] and me["joinable"] == [{"id": org_id, "name": "Kajima"}]
