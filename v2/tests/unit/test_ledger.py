"""Contract tests for the double-entry ledger, the payment seam, and exactly-once billing.

These pin the RULES money depends on, not the implementation:

  * the books always balance — an unbalanced posting is refused, never accepted-and-wrong
  * a replayed usage write bills once (the bug this phase closes: a lost response looks
    identical to a failed write, so the proxy retries and used to insert a second charge)
  * a purchase splits into fees / reserved inference / distributable margin, and the reserve
    is EXACT rather than estimated — which only holds because the spend cap is hard
  * promotional credits create a liability but NEVER a creator accrual (else it prints money)
  * expiry converts an unspent liability into breakage revenue and releases its reserve

Both services are standalone directories rather than importable packages, so each is loaded by
path — same approach as test_metering.py.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

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
def led(monkeypatch):
    monkeypatch.setenv("AGENTD_CREDITS_PER_USD", "166667")
    monkeypatch.setenv("AGENTD_CREDIT_MARKUP", "2.0")
    monkeypatch.setenv("AGENTD_PROCESSING_FEE_PCT", "0.03")
    monkeypatch.setenv("AGENTD_CREATOR_SHARE_PCT", "0.80")
    monkeypatch.delenv("AGENTD_ALLOW_NEGATIVE_MARGIN", raising=False)
    return _load(V2 / "accounts" / "ledger.py", "agentd_ledger")


@pytest.fixture
def book(led):
    """An in-memory ledger, no HTTP."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    led.schema(c)
    return c


@pytest.fixture
def accounts(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTD_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "0/0")
    monkeypatch.setenv("ACCOUNTS_INTERNAL_KEY", "devinternal")
    monkeypatch.setenv("AGENTD_TELEMETRY", "0")
    monkeypatch.setenv("AGENTD_CREDITS_PER_USD", "166667")
    monkeypatch.setenv("AGENTD_CREDIT_MARKUP", "2.0")
    module = _load(ACCOUNTS_APP, "agentd_accounts_app_ledger")
    with TestClient(module.app) as client:
        r = client.post("/signup", json={"email": "L@x.io", "password": "password123"})
        # The module comes out too: making a subscription fall due needs the clock moved, and
        # editing renews_at directly is honest test setup where sleeping 30 days is not.
        client.app_module = module  # type: ignore[attr-defined]
        yield client, r.json()["account_id"]


def _make_due(client, seconds_ago: float = 60) -> None:
    """Backdate every active subscription so the next renew-due run picks it up.

    `seconds_ago` must DIFFER between calls when a test simulates consecutive periods: the
    renewal key is the whole-second `renews_at` it renews into, and a test runs fast enough that
    two `now - 60` values land in the same second, which is correctly treated as one period.
    """
    with client.app_module._db() as c:  # type: ignore[attr-defined]
        c.execute("UPDATE subscriptions SET renews_at = ? WHERE status = 'active'",
                  (client.app_module._now() - seconds_ago,))  # type: ignore[attr-defined]


# --- the books must balance ---------------------------------------------------


def test_an_unbalanced_posting_is_refused(book, led):
    """Refusing is the feature. A ledger that accepts a bad posting is worse than one that
    errors, because the mistake becomes invisible and compounds."""
    with pytest.raises(led.LedgerError, match="unbalanced"):
        led.post(book, "bogus", [("cash", led.DEBIT, 100), ("platform_revenue", led.CREDIT, 99)], 1.0)
    assert book.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0] == 0


def test_unknown_accounts_and_bad_amounts_are_refused(book, led):
    with pytest.raises(led.LedgerError, match="unknown account"):
        led.post(book, "x", [("not_a_real_account", led.DEBIT, 1), ("cash", led.CREDIT, 1)], 1.0)
    with pytest.raises(led.LedgerError, match="positive"):
        led.post(book, "x", [("cash", led.DEBIT, 0), ("platform_revenue", led.CREDIT, 0)], 1.0)


def test_idempotency_key_posts_once(book, led):
    a, created_a = led.post(
        book, "purchase", [("cash", led.DEBIT, 10), ("user_credit_liability", led.CREDIT, 10)],
        1.0, idempotency_key="same",
    )
    b, created_b = led.post(
        book, "purchase", [("cash", led.DEBIT, 10), ("user_credit_liability", led.CREDIT, 10)],
        2.0, idempotency_key="same",
    )
    assert created_a and not created_b
    assert a == b, "a replay must return the ORIGINAL transaction, not a new one"
    assert book.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0] == 2


# --- a purchase splits three ways --------------------------------------------


def test_the_reserve_is_exact_not_estimated(led):
    """The whole point of the hard cap (D2): worst-case inference for a grant is COMPUTABLE at
    the moment of sale, so reserving it is arithmetic rather than a bet on future usage."""
    credits = led.credits_for_usd(20.0)          # $20 at 2x markup => $10 of provider cost
    split = led.split_purchase(led.usd_to_micros(20.0), credits)

    assert led.micros_to_usd(split["reserve_micros"]) == pytest.approx(10.0, abs=0.01)
    assert led.micros_to_usd(split["fee_micros"]) == pytest.approx(0.60, abs=0.01)
    # gross - fee - reserve, split 80/20
    assert led.micros_to_usd(split["margin_micros"]) == pytest.approx(9.40, abs=0.01)
    assert led.micros_to_usd(split["creator_micros"]) == pytest.approx(7.52, abs=0.01)
    assert split["creator_micros"] + split["platform_micros"] == split["margin_micros"]


def test_markup_of_one_means_zero_margin_and_is_refused(book, led, monkeypatch):
    """Selling credits at cost is the easy mistake: `credits_for(cost)` and the sale rate are
    the same number, so without a markup $20 buys exactly $20 of inference. The books would
    balance perfectly while the business lost the processing fee on every sale."""
    monkeypatch.setenv("AGENTD_CREDIT_MARKUP", "1.0")
    credits = led.credits_for_usd(20.0)
    with pytest.raises(led.LedgerError, match="would lose"):
        led.post_purchase(book, 1.0, account_id="a1",
                          gross_micros=led.usd_to_micros(20.0), credits_sold=credits)

    monkeypatch.setenv("AGENTD_ALLOW_NEGATIVE_MARGIN", "1")
    _txn, created, split = led.post_purchase(
        book, 1.0, account_id="a1", gross_micros=led.usd_to_micros(20.0), credits_sold=credits
    )
    assert created and split["margin_micros"] < 0, "explicitly permitted, still recorded honestly"


def test_purchase_leaves_the_books_balanced(book, led):
    credits = led.credits_for_usd(20.0)
    led.post_purchase(book, 1.0, account_id="a1", gross_micros=led.usd_to_micros(20.0),
                      credits_sold=credits, creator_id="bob", agent_id="agent-x")
    b = led.balances(book)
    assert b["balanced"], b
    # Prepaid credits are a LIABILITY on arrival, not revenue.
    assert b["accounts"]["user_credit_liability"] == pytest.approx(20.0, abs=0.01)
    assert b["accounts"]["platform_revenue"] == 0.0
    assert b["accounts"]["creator_payable"] == pytest.approx(7.52, abs=0.01)
    assert b["accounts"]["inference_reserve"] == pytest.approx(10.0, abs=0.01)


def test_promotional_credits_never_accrue_to_a_creator(book, led):
    """Free credits plus a creator payout is a money printer: mint promo credits, spend them on
    your own agent, get paid real money."""
    led.post_promotional_grant(book, 1.0, account_id="a1", credits=1_000_000, agent_id="agent-x")
    b = led.balances(book)
    assert b["balanced"]
    assert b["accounts"]["creator_payable"] == 0.0
    assert b["accounts"]["cash"] == 0.0, "nothing was paid, so no cash moved"
    assert b["accounts"]["user_credit_liability"] > 0, "but we still owe the service"


# --- consumption and expiry ---------------------------------------------------


def test_consumption_bills_the_provider_and_recognises_revenue(book, led):
    credits = led.credits_for_usd(20.0)
    led.post_purchase(book, 1.0, account_id="a1", gross_micros=led.usd_to_micros(20.0),
                      credits_sold=credits)
    led.post_consumption(book, 2.0, account_id="a1", cost_micros=led.usd_to_micros(0.50),
                         credits_charged=83_334)  # ~$0.50 of credits at 166_667/USD

    b = led.balances(book)
    assert b["balanced"], b
    assert b["accounts"]["provider_cost"] == pytest.approx(0.50, abs=0.01)
    # The reserve is what pays the provider, so it draws down as inference is delivered.
    assert b["accounts"]["inference_reserve"] == pytest.approx(9.50, abs=0.01)
    assert b["accounts"]["platform_revenue"] == pytest.approx(0.50, abs=0.01)
    assert b["accounts"]["user_credit_liability"] == pytest.approx(19.50, abs=0.01)


def test_expiry_turns_unspent_credits_into_breakage(book, led):
    credits = led.credits_for_usd(20.0)
    led.post_purchase(book, 1.0, account_id="a1", gross_micros=led.usd_to_micros(20.0),
                      credits_sold=credits)
    _txn, created = led.post_expiry(book, 5.0, account_id="a1", credits_unused=credits, grant_id=7)
    assert created

    b = led.balances(book)
    assert b["balanced"], b
    assert b["accounts"]["user_credit_liability"] == pytest.approx(10.0, abs=0.01), \
        "only the part backed by inference is discharged; the markup was never a liability"
    assert b["accounts"]["breakage_revenue"] == pytest.approx(10.0, abs=0.01)
    # Inference that will now never happen releases its reserve back to cash.
    assert b["accounts"]["inference_reserve"] == pytest.approx(0.0, abs=0.01)

    # Running the close-out twice must not book the same breakage again.
    _txn2, created2 = led.post_expiry(book, 6.0, account_id="a1", credits_unused=credits, grant_id=7)
    assert not created2


# --- exactly-once billing over HTTP -------------------------------------------


def _usage(client, account_id, **over):
    body = {"account_id": account_id, "model": "m", "in_tokens": 10, "out_tokens": 5,
            "cost_usd": 0.01, "credits": 1667, "run_id": "r1", "turn_id": "r1-1"}
    body.update(over)
    return client.post("/usage", json=body, headers=INTERNAL)


def test_replayed_usage_write_bills_exactly_once(accounts):
    """THE BUG THIS CLOSES. The proxy buffers and replays a usage write when it does not get a
    2xx — and 'no response' includes 'accounts committed the row, then the reply was lost'.
    Without a key the replay silently doubles that account's recorded spend."""
    client, account_id = accounts

    first = _usage(client, account_id, event_id="evt-1")
    assert first.status_code == 200 and first.json()["duplicate"] is False

    second = _usage(client, account_id, event_id="evt-1")
    assert second.status_code == 200, "a replay is not an error — the proxy did the right thing"
    assert second.json()["duplicate"] is True

    rows = client.get(f"/budget/{account_id}", headers=INTERNAL).json()
    assert rows["spent_usd"] == pytest.approx(0.01), "charged once, not twice"

    entries = client.get("/ledger/entries", params={"txn_type": "consumption"},
                         headers=INTERNAL).json()["entries"]
    assert len({e["txn_id"] for e in entries}) == 1, "and the ledger posted once too"


def test_usage_without_an_event_id_still_records(accounts):
    """An older proxy sends no key. Losing a billing record is worse than duplicating one, so
    the write must still land rather than be rejected."""
    client, account_id = accounts
    assert _usage(client, account_id).status_code == 200
    assert _usage(client, account_id).status_code == 200
    assert client.get(f"/budget/{account_id}", headers=INTERNAL).json()["spent_usd"] == pytest.approx(0.02)


# --- the purchase endpoint ----------------------------------------------------


def test_purchase_is_idempotent_and_credits_land_once(accounts):
    client, account_id = accounts
    body = {"account_id": account_id, "usd": 20.0, "idempotency_key": "buy-1"}

    first = client.post("/purchase", json=body, headers=INTERNAL).json()
    assert first["ok"] and first["replayed"] is False
    credits_after_first = first["credits_remaining"]

    second = client.post("/purchase", json=body, headers=INTERNAL).json()
    assert second["replayed"] is True
    assert second["txn_id"] == first["txn_id"]
    assert second["credits_remaining"] == credits_after_first, "no second grant"

    b = client.get("/ledger/balances", headers=INTERNAL).json()
    assert b["balanced"]
    assert b["accounts"]["cash"] == pytest.approx(20.0 - 0.60 - 10.0, abs=0.05)


def test_buying_an_agent_product_accrues_to_its_creator_and_entitles_the_buyer(accounts):
    client, account_id = accounts
    client.post("/products", headers=INTERNAL, json={
        "id": "figure-pro", "kind": "agent_subscription", "creator_id": "bob",
        "agent_id": "figure-creator", "price_usd": 20.0, "scope": "agent:figure-creator",
    })
    r = client.post("/purchase", headers=INTERNAL, json={
        "account_id": account_id, "product_id": "figure-pro", "idempotency_key": "buy-fig",
    }).json()
    assert r["ok"]
    # Credits are siloed to that agent, so the subscription cannot be spent elsewhere.
    assert r["funding_source"] == "agent_subscription"

    ent = client.get("/entitlement", headers=INTERNAL,
                     params={"account_id": account_id, "agent_id": "figure-creator"}).json()
    assert ent["entitled"] is True and ent["source"] == "purchase"

    b = client.get("/ledger/balances", headers=INTERNAL).json()
    assert b["balanced"]
    assert b["accounts"]["creator_payable"] > 0


def test_the_mocked_rail_records_intent_and_moves_nothing(accounts, monkeypatch):
    client, account_id = accounts
    r = client.post("/purchase", headers=INTERNAL,
                    json={"account_id": account_id, "usd": 5.0, "idempotency_key": "k1"}).json()
    assert r["charge_reference"].startswith("null_ch_"), "obviously fake, never mistakable for real"


# --- entitlements gate on being SOLD, not on a list in code -------------------


def test_an_agent_nobody_sells_needs_no_entitlement(accounts):
    """The default agent and every first-party agent must stay freely runnable. Requiring an
    entitlement for all of them would gate the whole product behind a marketplace it does not
    have yet."""
    client, account_id = accounts
    view = client.get("/funding", headers=INTERNAL,
                      params={"account_id": account_id, "agent_id": "main"}).json()
    assert view["entitlement_required"] is False
    assert view["entitled"] is True


def test_listing_an_agent_for_sale_is_what_starts_gating_it(accounts):
    client, account_id = accounts
    client.post("/products", headers=INTERNAL, json={
        "id": "paid-agent", "kind": "agent_subscription", "creator_id": "bob",
        "agent_id": "gated", "price_usd": 20.0, "scope": "agent:gated",
    })
    before = client.get("/funding", headers=INTERNAL,
                        params={"account_id": account_id, "agent_id": "gated"}).json()
    assert before["entitlement_required"] is True
    assert before["entitled"] is False, "product exists, this account has not bought it"

    client.post("/purchase", headers=INTERNAL, json={
        "account_id": account_id, "product_id": "paid-agent", "idempotency_key": "b1"})
    after = client.get("/funding", headers=INTERNAL,
                       params={"account_id": account_id, "agent_id": "gated"}).json()
    assert after["entitled"] is True


# --- renewals -----------------------------------------------------------------


def _subscribe(client, account_id, *, period_days=30):
    client.post("/products", headers=INTERNAL, json={
        "id": "monthly", "kind": "agent_subscription", "creator_id": "bob",
        "agent_id": "figure-creator", "price_usd": 20.0, "scope": "agent:figure-creator",
        "period_days": period_days,
    })
    return client.post("/purchase", headers=INTERNAL, json={
        "account_id": account_id, "product_id": "monthly", "idempotency_key": "sub-1"}).json()


def _purchase_txn_count(client) -> int:
    entries = client.get("/ledger/entries", headers=INTERNAL,
                         params={"txn_type": "purchase", "limit": 1000}).json()["entries"]
    return len({e["txn_id"] for e in entries})


def test_renewal_does_nothing_before_the_period_ends(accounts):
    client, account_id = accounts
    _subscribe(client, account_id)
    assert client.post("/subscriptions/renew-due", headers=INTERNAL).json()["renewed"] == 0


def test_renewal_charges_once_per_period(accounts):
    """Two runs in the same period must charge once. The key is the period being renewed INTO,
    so a later period charges again — which is the difference between a subscription and a single
    sale that keeps re-billing."""
    client, account_id = accounts
    _subscribe(client, account_id)
    _make_due(client, 3600)
    before = _purchase_txn_count(client)

    assert client.post("/subscriptions/renew-due", headers=INTERNAL).json()["renewed"] == 1
    assert _purchase_txn_count(client) == before + 1

    # Renewal pushed renews_at 30 days out, so an immediate re-run finds nothing due at all.
    assert client.post("/subscriptions/renew-due", headers=INTERNAL).json()["renewed"] == 0
    assert _purchase_txn_count(client) == before + 1
    assert client.get("/ledger/balances", headers=INTERNAL).json()["balanced"]

    # Force the SAME period due again: the scheduler ran twice. One charge, reported honestly as
    # already_charged rather than as new revenue.
    _make_due(client, 3600)
    again = client.post("/subscriptions/renew-due", headers=INTERNAL).json()
    assert again["renewed"] == 0 and again["already_charged"] == 1
    assert _purchase_txn_count(client) == before + 1

    # A genuinely NEW period is a new charge — otherwise a subscription bills only once, ever.
    _make_due(client, 7200)
    assert client.post("/subscriptions/renew-due", headers=INTERNAL).json()["renewed"] == 1
    assert _purchase_txn_count(client) == before + 2


def test_a_nonpositive_period_is_skipped_not_billed_forever(accounts):
    """A product with period_days <= 0 can never move renews_at into the future, so it would be
    'due' on every single run and bill every time. Skip it instead."""
    client, account_id = accounts
    _subscribe(client, account_id, period_days=-1)
    _make_due(client)
    r = client.post("/subscriptions/renew-due", headers=INTERNAL).json()
    assert r["renewed"] == 0 and r["skipped"] == 1


def test_a_renewal_accrues_to_the_creator_again(accounts):
    """A creator earns every period, not only on the first sale."""
    client, account_id = accounts
    _subscribe(client, account_id)
    _make_due(client)
    before = client.get("/ledger/balances", headers=INTERNAL).json()["accounts"]["creator_payable"]
    client.post("/subscriptions/renew-due", headers=INTERNAL)
    after = client.get("/ledger/balances", headers=INTERNAL).json()["accounts"]["creator_payable"]
    assert after > before


# --- the balance-sheet snapshot and readiness ---------------------------------


def test_snapshot_reports_the_cost_ratio_and_stays_balanced(accounts):
    client, account_id = accounts
    client.post("/purchase", headers=INTERNAL,
                json={"account_id": account_id, "usd": 20.0, "idempotency_key": "s1"})
    _usage(client, account_id, event_id="e-snap", cost_usd=0.60, credits=100_000)

    snap = client.post("/ledger/snapshot", headers=INTERNAL).json()
    assert snap["balanced"] is True
    assert snap["accounts"]["inference_reserve"] > 0
    assert snap["credits_outstanding"] > 0


def test_readiness_requires_a_writable_database(accounts):
    """/health is liveness and must not depend on the DB; this one must. An EFS mount that has
    gone read-only otherwise stays hidden until the first user tries to sign up."""
    client, _account_id = accounts
    assert client.get("/health").json()["ok"] is True
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["db"] == "writable"


def test_close_expired_books_breakage_and_is_safe_to_rerun(accounts):
    client, account_id = accounts
    # expires_days is negative => already expired (the fail-closed branch in /grant)
    client.post("/grant", headers=INTERNAL,
                json={"account_id": account_id, "credits": 500_000, "expires_days": -1})

    first = client.post("/ledger/close-expired", headers=INTERNAL).json()
    assert first["grants_closed"] == 1 and first["credits_expired"] == 500_000

    second = client.post("/ledger/close-expired", headers=INTERNAL).json()
    assert second["grants_closed"] == 0, "already booked; re-running must not double-count"

    b = client.get("/ledger/balances", headers=INTERNAL).json()
    assert b["balanced"] and b["accounts"]["breakage_revenue"] > 0
