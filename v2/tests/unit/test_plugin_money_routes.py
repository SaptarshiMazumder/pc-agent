"""The money routes a daemon's plugin calls: /credits and /debit, with both credentials.

Replays 2026-09-27. A sandboxed plugin sends the daemon's internal key as
`Authorization: Bearer <key>` (the daemon substitutes it into that header). /debit and /budget
only read `X-Internal-Key`, so on every hosted daemon the Comfy balance gate failed open and every
paid run's debit came back 401 — never charged. On a desktop the gate read /budget, which has no
credits in it, and refused every paid run as "the account has 0".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

V2 = Path(__file__).resolve().parents[2]
KEY = "devinternal"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def accounts(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTD_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("AGENTD_AUTH_ISSUER", "https://accounts.test.invalid")
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "0/0")
    monkeypatch.setenv("ACCOUNTS_INTERNAL_KEY", KEY)
    monkeypatch.setenv("AGENTD_TELEMETRY", "0")
    module = _load(V2 / "accounts" / "app.py", "agentd_accounts_app_money_routes")
    with TestClient(module.app) as client:
        acct = {}
        for email in ("me@x.io", "other@x.io"):
            r = client.post("/signup", json={"email": email, "password": "password123"})
            assert r.status_code == 200, r.text
            token = client.post("/login", json={"email": email, "password": "password123"}).json()["access_token"]
            acct[email] = (r.json()["account_id"], token)
        me, _ = acct["me@x.io"]
        r = client.post("/grant", json={"account_id": me, "credits": 50_000}, headers={"X-Internal-Key": KEY})
        assert r.status_code == 200, r.text
        yield client, acct


def _balance(client, account_id, headers) -> int:
    r = client.get(f"/credits/{account_id}", headers=headers)
    assert r.status_code == 200, r.text
    return int(r.json()["credits_remaining"])


def test_credits_answers_in_credits_for_every_legitimate_caller(accounts):
    client, acct = accounts
    me, token = acct["me@x.io"]
    header = _balance(client, me, {"X-Internal-Key": KEY})
    assert header > 0
    assert _balance(client, me, {"Authorization": f"Bearer {KEY}"}) == header  # a hosted plugin
    assert _balance(client, me, {"Authorization": f"Bearer {token}"}) == header  # a desktop


def test_a_person_cannot_read_someone_elses_credits(accounts):
    client, acct = accounts
    me, _ = acct["me@x.io"]
    _, other_token = acct["other@x.io"]
    r = client.get(f"/credits/{me}", headers={"Authorization": f"Bearer {other_token}"})
    assert r.status_code == 403


def test_a_hosted_plugins_debit_is_charged(accounts):
    client, acct = accounts
    me, _ = acct["me@x.io"]
    before = _balance(client, me, {"X-Internal-Key": KEY})
    r = client.post(
        "/debit",
        json={"account_id": me, "agent_id": "comfy-artchitect", "credits": 1_000},
        headers={"Authorization": f"Bearer {KEY}"},
    )
    assert r.status_code == 200, r.text
    assert _balance(client, me, {"X-Internal-Key": KEY}) == before - 1_000


def test_a_desktop_debits_only_itself(accounts):
    client, acct = accounts
    me, _ = acct["me@x.io"]
    _, other_token = acct["other@x.io"]
    r = client.post(
        "/debit",
        json={"account_id": me, "credits": 1_000},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert r.status_code == 403
