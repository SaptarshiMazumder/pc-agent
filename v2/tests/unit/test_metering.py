"""Contract tests for prepaid credits — conversion, the hard cap, tiers, and ledger replay.

These pin the RULES that money depends on, not the implementation:

  * credits derive from provider cost, so a model added tomorrow prices itself correctly
  * the cap is HARD — no overdraft, no partial debit (what makes worst-case cost knowable)
  * a grant's tier ceiling is enforced BEFORE the provider is called
  * a failed ledger write is buffered and replayed, never silently dropped

Both services are standalone directories rather than importable packages, so each is loaded by
path — same approach as test_accounts_service.py / test_model_proxy_service.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

V2 = Path(__file__).resolve().parents[2]
ACCOUNTS_APP = V2 / "accounts" / "app.py"
INTERNAL = {"X-Internal-Key": "devinternal"}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def accounts(monkeypatch, tmp_path):
    """A fresh accounts service on a throwaway DB, with the internal key enforced."""
    monkeypatch.setenv("AGENTD_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "0/0")
    monkeypatch.setenv("ACCOUNTS_INTERNAL_KEY", "devinternal")
    monkeypatch.setenv("AGENTD_TELEMETRY", "0")
    module = _load(ACCOUNTS_APP, "agentd_accounts_app_metering")
    with TestClient(module.app) as client:
        r = client.post("/signup", json={"email": "m@x.io", "password": "password123"})
        yield client, r.json()["account_id"]


@pytest.fixture
def metering(monkeypatch):
    monkeypatch.setenv("AGENTD_TELEMETRY", "0")
    return _load(V2 / "model_proxy" / "metering.py", "agentd_metering")


# --- credits are derived from cost, not from a multiplier table ---------------


def test_credits_scale_with_real_provider_cost(metering, monkeypatch):
    monkeypatch.setenv("AGENTD_CREDITS_PER_USD", "166667")
    cheap = metering.credits_for(0.0036)  # a DeepSeek-shaped call
    dear = metering.credits_for(0.30)  # the same call on a premium model
    assert cheap == 601
    assert dear == 50001
    # The ~83x spread is what makes a flat "1M tokens" promise unsurvivable, and it falls out
    # of provider pricing rather than being a number anyone maintains.
    assert dear / cheap > 50


def test_a_real_call_is_never_free(metering):
    """Sub-credit calls round UP to 1. A call that cost us money must cost the user something."""
    assert metering.credits_for(1e-9) == 1
    assert metering.credits_for(0) == 0  # ...but a call that cost nothing charges nothing


def test_credits_dial_is_config_not_code(metering, monkeypatch):
    monkeypatch.setenv("AGENTD_CREDITS_PER_USD", "1000")
    assert metering.credits_for(1.0) == 1000
    monkeypatch.setenv("AGENTD_CREDITS_PER_USD", "not-a-number")
    assert metering.credits_for(1.0) > 0  # bad config must not make everything free


# --- model tiers --------------------------------------------------------------


def test_unknown_models_land_in_the_middle_tier(metering, monkeypatch):
    """Not the cheapest (a new model would become spendable on a cheap-only grant) and not the
    dearest (ordinary use would break until someone edits config)."""
    monkeypatch.delenv("AGENTD_MODEL_TIERS", raising=False)
    assert metering.tier_for("something/nobody-configured") == "standard"


def test_tier_ceiling_blocks_expensive_models(metering, monkeypatch):
    monkeypatch.setenv(
        "AGENTD_MODEL_TIERS", '{"deepseek": "cheap", "flash": "cheap", "opus": "premium"}'
    )
    assert metering.tier_allowed("deepseek/deepseek-chat", "cheap") is True
    assert metering.tier_allowed("anthropic/claude-opus", "cheap") is False
    assert metering.tier_allowed("anthropic/claude-opus", "premium") is True
    assert metering.tier_allowed("anthropic/claude-opus", "") is True  # no ceiling = unrestricted


def test_unknown_tier_name_fails_open(metering, monkeypatch):
    """A typo in config must not lock a paying customer out of every model."""
    monkeypatch.setenv("AGENTD_MODEL_TIERS", '{"opus": "premium"}')
    assert metering.tier_allowed("anthropic/claude-opus", "typo-tier") is True


# --- the hard cap -------------------------------------------------------------


def test_grant_then_spend_then_hard_stop(accounts):
    """The cap: spend down, DRAIN on the boundary call, refuse from zero.

    The middle step is the one with an incident behind it. /debit used to REFUSE a charge the
    balance could not fully cover — and since every real charge exceeded the small balance, the
    balance never moved, the pre-call gate (which closes at zero) never engaged, and a 13-credit
    account chatted for free indefinitely. Draining bounds the leak to one call's shortfall and
    guarantees the next call is refused BEFORE the provider is touched.
    """
    client, account_id = accounts
    client.post("/grant", json={"account_id": account_id, "credits": 100}, headers=INTERNAL)

    view = client.get("/funding", params={"account_id": account_id}, headers=INTERNAL).json()
    assert view["credits_remaining"] == 100
    assert view["funding_source"] == "platform_pool"

    ok = client.post("/debit", json={"account_id": account_id, "credits": 60}, headers=INTERNAL)
    assert ok.status_code == 200
    assert ok.json()["credits_remaining"] == 40
    assert ok.json()["ok"] is True
    assert ok.json()["shortfall"] == 0

    # The boundary call: 41 against 40. The call already ran, so the money is spent either way —
    # the balance is drained to zero and the uncovered credit is REPORTED, never overdrafted.
    over = client.post("/debit", json={"account_id": account_id, "credits": 41}, headers=INTERNAL)
    assert over.status_code == 200
    body = over.json()
    assert body["ok"] is False
    assert body["drained"] == 40
    assert body["shortfall"] == 1
    assert body["credits_remaining"] == 0

    # NO OVERDRAFT, still: zero is the floor, and from zero a debit is refused outright. This is
    # the invariant the business rests on — worst-case cost per subscription stays knowable.
    from_zero = client.post("/debit", json={"account_id": account_id, "credits": 1}, headers=INTERNAL)
    assert from_zero.status_code == 402
    after = client.get("/funding", params={"account_id": account_id}, headers=INTERNAL).json()
    assert after["credits_remaining"] == 0


def test_expired_grants_are_not_spendable(accounts):
    client, account_id = accounts
    client.post(
        "/grant",
        json={"account_id": account_id, "credits": 500, "expires_days": -1},
        headers=INTERNAL,
    )
    view = client.get("/funding", params={"account_id": account_id}, headers=INTERNAL).json()
    assert view["credits_remaining"] == 0


def test_soonest_expiring_grant_is_spent_first(accounts):
    """Use-it-or-lose-it: draining the expiring grant first maximises what the user actually
    gets, and keeps breakage meaning genuine non-use."""
    client, account_id = accounts
    client.post(
        "/grant",
        json={"account_id": account_id, "credits": 50, "expires_days": 365},
        headers=INTERNAL,
    )
    client.post(
        "/grant", json={"account_id": account_id, "credits": 50, "expires_days": 1}, headers=INTERNAL
    )
    client.post("/debit", json={"account_id": account_id, "credits": 50}, headers=INTERNAL)

    grants = client.get("/funding", params={"account_id": account_id}, headers=INTERNAL).json()
    assert grants["credits_remaining"] == 50  # the long-dated one survives


def test_agent_scoped_credits_are_not_spendable_elsewhere(accounts):
    """A paid agent's bundled allowance is its own silo — it cannot subsidise another agent."""
    client, account_id = accounts
    client.post(
        "/grant",
        json={"account_id": account_id, "credits": 200, "scope": "agent:figure-creator"},
        headers=INTERNAL,
    )
    mine = client.get(
        "/funding", params={"account_id": account_id, "agent_id": "figure-creator"}, headers=INTERNAL
    ).json()
    assert mine["credits_remaining"] == 200
    assert mine["funding_source"] == "agent_subscription"

    other = client.get(
        "/funding", params={"account_id": account_id, "agent_id": "something-else"}, headers=INTERNAL
    ).json()
    assert other["credits_remaining"] == 0


def test_promotional_grant_carries_its_class_and_tier_ceiling(accounts):
    """Promotional credits must be distinguishable (they can never be revenue-shareable) and
    are the case where a tier ceiling matters most — a free agent's creator has no cost
    incentive of their own."""
    client, account_id = accounts
    client.post(
        "/grant",
        json={
            "account_id": account_id,
            "credits": 50_000,
            "credit_class": "promotional",
            "model_tier_max": "cheap",
        },
        headers=INTERNAL,
    )
    view = client.get("/funding", params={"account_id": account_id}, headers=INTERNAL).json()
    assert view["credit_class"] == "promotional"
    assert view["model_tier_max"] == "cheap"


def test_funding_endpoints_require_the_internal_key(accounts):
    """Balances and debits are trusted-infra only; a desktop must not be able to grant itself
    credits or forge a debit."""
    client, account_id = accounts
    assert client.get("/funding", params={"account_id": account_id}).status_code == 401
    assert client.post("/debit", json={"account_id": account_id, "credits": 1}).status_code == 401
    assert client.post("/grant", json={"account_id": account_id, "credits": 1}).status_code == 401


# --- the ledger row carries the tracking number and the price ----------------


def test_usage_row_records_both_cost_and_credits(accounts):
    client, account_id = accounts
    r = client.post(
        "/usage",
        json={
            "account_id": account_id,
            "model": "deepseek/deepseek-chat",
            "in_tokens": 1200,
            "out_tokens": 300,
            "cost_usd": 0.004,
            "credits": 667,
            "funding_source": "platform_pool",
            "run_id": "RUN-1",
            "turn_id": "RUN-1-2",
        },
        headers=INTERNAL,
    )
    assert r.status_code == 200
    assert r.json()["spent_usd"] == pytest.approx(0.004)


# --- ledger replay (plan 1.6) -------------------------------------------------


@pytest.mark.asyncio
async def test_failed_ledger_rows_are_replayed_when_accounts_returns(metering):
    """DEF-1's other half. Swallowing the failure is right; losing the row is not."""
    posted: list[dict] = []
    fail = {"on": True}

    class FakeClient:
        async def post(self, path, headers=None, json=None):
            if fail["on"]:
                raise __import__("httpx").HTTPError("accounts down")
            posted.append(json)
            return SimpleNamespace(status_code=200)

    row = {"account_id": "a1", "cost_usd": 0.01, "run_id": "RUN-9"}
    assert metering.buffer_row(row) == 1
    assert metering.buffer_depth() == 1

    # still down: the row stays queued, and we stop at the first failure rather than hammering
    replayed, left = await metering.drain_buffer(FakeClient(), "k")
    assert (replayed, left) == (0, 1)

    fail["on"] = False
    replayed, left = await metering.drain_buffer(FakeClient(), "k")
    assert (replayed, left) == (1, 0)
    assert posted == [row]


@pytest.mark.asyncio
async def test_buffer_is_bounded(metering, monkeypatch):
    """An unbounded retry queue turns a downstream outage into an out-of-memory kill."""
    assert metering._buffer.maxlen is not None
    for i in range(metering._buffer.maxlen + 50):
        metering.buffer_row({"i": i})
    assert metering.buffer_depth() == metering._buffer.maxlen


# --- the pre-call gate: plan DEF-2 --------------------------------------------
#
# Budgets used to be enforced NOWHERE for desktop Cloud users: the daemon's check requires
# accounts-mode (false on every desktop) and the proxy never checked at all. These pin the fix
# at the proxy — the one chokepoint neither topology can bypass.


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setenv("AGENTD_TELEMETRY", "0")
    monkeypatch.setenv("ACCOUNTS_URL", "http://accounts.test")
    monkeypatch.setenv("ACCOUNTS_INTERNAL_KEY", "devinternal")
    monkeypatch.setenv("AGENTD_MODEL_TIERS", '{"deepseek": "cheap", "opus": "premium"}')
    return _load(V2 / "model_proxy" / "custom_auth.py", "agentd_custom_auth_metering")


def _hook_args(model: str, agent_id: str = ""):
    headers = {"x-agentd-run-id": "RUN-1", "x-agentd-agent-id": agent_id}
    return {
        "user_api_key_dict": SimpleNamespace(user_id="acct_1", parent_otel_span=None),
        "cache": None,
        "data": {"model": model, "metadata": {"headers": headers}},
        "call_type": "acompletion",
    }


def _with_funding(auth, monkeypatch, view):
    async def fake_funding(account_id, agent_id):
        return view

    monkeypatch.setattr(auth, "_funding", fake_funding)


@pytest.mark.asyncio
async def test_call_allowed_when_credits_remain(auth, monkeypatch):
    _with_funding(auth, monkeypatch, {"credits_remaining": 5000, "model_tier_max": ""})
    assert await auth.usage_logger_instance.async_pre_call_hook(
        **_hook_args("deepseek/deepseek-chat")
    ) is None


@pytest.mark.asyncio
async def test_exhausted_balance_is_refused_before_the_provider_is_called(auth, monkeypatch):
    """402, raised in the PRE-call hook — so no provider request is made and no money spent.

    `credits_enforced` is what makes a zero balance mean EXHAUSTED: this account was granted
    credits and used them up."""
    from fastapi import HTTPException

    _with_funding(
        auth,
        monkeypatch,
        {"credits_remaining": 0, "model_tier_max": "", "credits_enforced": True},
    )
    with pytest.raises(HTTPException) as excinfo:
        await auth.usage_logger_instance.async_pre_call_hook(**_hook_args("deepseek/deepseek-chat"))
    assert excinfo.value.status_code == 402


@pytest.mark.asyncio
async def test_an_account_never_granted_credits_is_not_refused(auth, monkeypatch):
    """THE NO-LOCKOUT GUARANTEE. Zero balance is ambiguous: exhausted, or never on a credit plan
    at all? Every existing account is the second — grants are minted only by a purchase or an
    explicit grant call, so the moment this gate started firing it would have refused every
    user's next message. Enforcement follows the DATA: no grant, no refusal."""
    _with_funding(
        auth,
        monkeypatch,
        {"credits_remaining": 0, "model_tier_max": "", "credits_enforced": False},
    )
    assert await auth.usage_logger_instance.async_pre_call_hook(
        **_hook_args("deepseek/deepseek-chat")
    ) is None


@pytest.mark.asyncio
async def test_an_older_accounts_build_without_the_field_fails_open(auth, monkeypatch):
    """Same rule the entitlement fields follow: absent means allowed."""
    _with_funding(auth, monkeypatch, {"credits_remaining": 0, "model_tier_max": ""})
    assert await auth.usage_logger_instance.async_pre_call_hook(
        **_hook_args("deepseek/deepseek-chat")
    ) is None


@pytest.mark.asyncio
async def test_require_credits_closes_the_free_tier(auth, monkeypatch):
    """The operator's switch for when signup grants credits and 'no grant' should mean 'no
    service' — off by default so nothing changes for a deployment that has not decided."""
    from fastapi import HTTPException

    monkeypatch.setenv("MODEL_PROXY_REQUIRE_CREDITS", "1")
    _with_funding(
        auth,
        monkeypatch,
        {"credits_remaining": 0, "model_tier_max": "", "credits_enforced": False},
    )
    with pytest.raises(HTTPException) as excinfo:
        await auth.usage_logger_instance.async_pre_call_hook(**_hook_args("deepseek/deepseek-chat"))
    assert excinfo.value.status_code == 402


@pytest.mark.asyncio
async def test_model_above_the_plan_tier_is_refused(auth, monkeypatch):
    from fastapi import HTTPException

    _with_funding(auth, monkeypatch, {"credits_remaining": 9999, "model_tier_max": "cheap"})
    with pytest.raises(HTTPException) as excinfo:
        await auth.usage_logger_instance.async_pre_call_hook(**_hook_args("anthropic/claude-opus"))
    assert excinfo.value.status_code == 403
    # ...while a cheap model on the same grant still runs
    assert await auth.usage_logger_instance.async_pre_call_hook(
        **_hook_args("deepseek/deepseek-chat")
    ) is None


@pytest.mark.asyncio
async def test_infra_calls_without_an_account_are_not_metered(auth, monkeypatch):
    """Master-key ops calls carry no account, so there is nothing to cap — they must not 402."""
    args = _hook_args("deepseek/deepseek-chat")
    args["user_api_key_dict"] = SimpleNamespace(user_id=None, parent_otel_span=None)
    assert await auth.usage_logger_instance.async_pre_call_hook(**args) is None


@pytest.mark.asyncio
async def test_metering_outage_fails_open(auth, monkeypatch):
    """A funding-lookup failure must not become a total outage for every paying user. The
    ledger counter + its alarm are what catch the resulting drift."""
    async def unavailable(account_id, agent_id):
        return None

    monkeypatch.setattr(auth, "_funding", unavailable)
    assert await auth.usage_logger_instance.async_pre_call_hook(
        **_hook_args("anthropic/claude-opus")
    ) is None
