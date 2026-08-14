"""Contract tests for the Stripe rail — the request we send, the signature we trust, and the
credits a callback grants.

NO NETWORK AND NO VENDOR SDK. `httpx.MockTransport` runs the real request-building code and
asserts on the bytes that would have left the machine, which is the part that can actually be
wrong. Signature verification is exercised against payloads signed here with the documented
scheme, so a forged, stale or rotated signature each get their own test rather than being
delegated to a library nobody reads.

THE END-TO-END TEST IS THE ONE THAT MATTERS: a checkout that returns a URL and grants nothing,
then a webhook that grants exactly once. That is the whole reason the rail could not be a drop-in
replacement for the mock.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
from fastapi.testclient import TestClient

from payments.application.interfaces.payment_gateway import (
    PaymentConfigurationError,
    PurchaseRequest,
)
from payments.application.interfaces.webhook_verifier import WebhookRejected
from payments.domain import payment_event, payment_status
from payments.domain.money import Money
from payments.infrastructure.stripe_api_client import StripeApiClient, form_encode
from payments.infrastructure.stripe_payment_gateway import StripePaymentGateway
from payments.infrastructure.stripe_webhook_verifier import StripeWebhookVerifier
from payments.main.payment_gateway_factory import build_payment_gateway, has_webhook

V2 = Path(__file__).resolve().parents[2]
ACCOUNTS_APP = V2 / "accounts" / "app.py"
WEBHOOK_SECRET = "whsec_test_secret"


# --- helpers -----------------------------------------------------------------


def _sign(body: bytes, *, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _session_event(
    *, event_id="evt_1", session_id="cs_test_1", amount_total=2000, metadata=None,
    kind="checkout.session.completed", payment_status_="paid",
) -> bytes:
    return json.dumps({
        "id": event_id,
        "type": kind,
        "data": {"object": {
            "id": session_id,
            "object": "checkout.session",
            "amount_total": amount_total,
            "currency": "usd",
            "payment_status": payment_status_,
            "client_reference_id": (metadata or {}).get("account_id", ""),
            "metadata": metadata or {},
        }},
    }).encode()


def _gateway_recording(captured: list, *, response: dict | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            status,
            json=response
            if response is not None
            else {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1",
                  "payment_status": "unpaid"},
        )

    return StripePaymentGateway(
        StripeApiClient("sk_test_x", transport=httpx.MockTransport(handler))
    )


# --- form encoding -----------------------------------------------------------


def test_nested_parameters_use_stripes_bracket_notation():
    """The one genuinely fiddly part of talking to Stripe without its SDK. Getting it wrong means
    the price silently does not reach them and the session is created for the wrong amount."""
    encoded = dict(form_encode({
        "mode": "payment",
        "metadata": {"account_id": "a1"},
        "line_items": [{"quantity": 1, "price_data": {"unit_amount": 2000}}],
    }))
    assert encoded["metadata[account_id]"] == "a1"
    assert encoded["line_items[0][quantity]"] == "1"
    assert encoded["line_items[0][price_data][unit_amount]"] == "2000"


def test_none_is_dropped_rather_than_sent_as_the_string_none():
    assert dict(form_encode({"a": None, "b": 1})) == {"b": "1"}


# --- creating a checkout -----------------------------------------------------


def test_a_purchase_is_pending_with_somewhere_to_send_the_customer():
    """THE BEHAVIOUR THE MOCK RAIL CANNOT HAVE. Nothing is granted here; the money becomes real
    on the callback."""
    intent = _gateway_recording([]).begin_purchase(
        PurchaseRequest(
            account_id="a1", amount=Money.from_usd(20.0), idempotency_key="click-1",
            success_url="https://app.example/ok", cancel_url="https://app.example/no",
        )
    )
    assert intent.status == payment_status.PENDING
    assert intent.awaiting_customer and intent.redirect_url.startswith("https://checkout.stripe")
    assert intent.reference == "cs_test_1"


def test_the_amount_and_the_order_reach_stripe():
    captured: list[httpx.Request] = []
    _gateway_recording(captured).begin_purchase(
        PurchaseRequest(
            account_id="a1", amount=Money.from_usd(19.99), idempotency_key="click-1",
            description="100k credits", success_url="https://app.example/ok",
            cancel_url="https://app.example/no", meta={"product_id": "credits-100k"},
        )
    )
    sent = dict(parse_qsl(captured[0].content.decode()))
    assert sent["line_items[0][price_data][unit_amount]"] == "1999", "cents, not dollars"
    assert sent["metadata[product_id]"] == "credits-100k"
    assert sent["client_reference_id"] == "a1"
    # Stripe replays the original response for 24h on a repeated key, which is what stops a
    # double-click becoming two charges.
    assert captured[0].headers["idempotency-key"] == "click-1"


def test_stripe_refusing_to_open_a_checkout_is_a_settled_failure_not_an_outage():
    """Nothing was charged and nothing will be, so the caller can show the rail's own words
    rather than a generic error — and must not retry."""
    gateway = _gateway_recording([], response={"error": {"message": "Invalid API Key provided",
                                                         "code": "api_key_invalid"}}, status=401)
    intent = gateway.begin_purchase(
        PurchaseRequest(account_id="a1", amount=Money.from_usd(20.0),
                        success_url="https://a/ok", cancel_url="https://a/no")
    )
    assert intent.failed and "Invalid API Key" in intent.detail


def test_a_checkout_without_return_urls_is_refused_before_the_call():
    with pytest.raises(PaymentConfigurationError, match="success_url"):
        _gateway_recording([]).begin_purchase(
            PurchaseRequest(account_id="a1", amount=Money.from_usd(20.0))
        )


def test_renewals_raise_rather_than_reporting_a_customers_card_as_declined():
    """A decline is a fact about the customer; this is a fact about us. Reporting our missing
    feature as their card failing sends the wrong dunning email to a paying subscriber."""
    with pytest.raises(PaymentConfigurationError, match="card on file"):
        _gateway_recording([]).charge_off_session(
            PurchaseRequest(account_id="a1", amount=Money.from_usd(20.0))
        )


# --- signature verification --------------------------------------------------


def test_a_forged_signature_is_rejected():
    """The only thing between a stranger and a free credit grant."""
    body = _session_event()
    verifier = StripeWebhookVerifier(WEBHOOK_SECRET)
    with pytest.raises(WebhookRejected, match="does not match"):
        verifier.verify(body, _sign(body, secret="whsec_someone_elses"))


def test_a_body_altered_after_signing_is_rejected():
    body = _session_event(amount_total=2000)
    signature = _sign(body)
    tampered = body.replace(b'"amount_total": 2000', b'"amount_total": 1')
    with pytest.raises(WebhookRejected):
        StripeWebhookVerifier(WEBHOOK_SECRET).verify(tampered, signature)


def test_a_captured_delivery_cannot_be_replayed_forever():
    """Without the timestamp check a valid delivery recorded once stays valid: the body never
    changes, so the signature never stops matching."""
    body = _session_event()
    old = _sign(body, timestamp=int(time.time()) - 3600)
    with pytest.raises(WebhookRejected, match="replay window"):
        StripeWebhookVerifier(WEBHOOK_SECRET).verify(body, old)


def test_both_signatures_are_accepted_during_a_secret_rotation():
    """Stripe signs with the old and new secret at once while a secret is being rotated. Reading
    only the first v1 would drop every delivery for the rotation window."""
    body = _session_event()
    ts = int(time.time())
    stale = hmac.new(b"whsec_old", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    good = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    event = StripeWebhookVerifier(WEBHOOK_SECRET).verify(body, f"t={ts},v1={stale},v1={good}")
    assert event.type == payment_event.PURCHASE_SUCCEEDED


def test_a_missing_header_is_rejected_rather_than_treated_as_unsigned():
    with pytest.raises(WebhookRejected, match="no Stripe-Signature"):
        StripeWebhookVerifier(WEBHOOK_SECRET).verify(_session_event(), "")


def test_a_completed_session_that_has_not_been_paid_grants_nothing_yet():
    """A delayed payment method completes the SESSION while the money is still in flight.
    Granting here hands over credits for a payment that can still fail days later."""
    body = _session_event(payment_status_="unpaid")
    event = StripeWebhookVerifier(WEBHOOK_SECRET).verify(body, _sign(body))
    assert event.type == payment_event.IGNORED


def test_the_later_async_success_is_what_grants():
    body = _session_event(kind="checkout.session.async_payment_succeeded")
    event = StripeWebhookVerifier(WEBHOOK_SECRET).verify(body, _sign(body))
    assert event.type == payment_event.PURCHASE_SUCCEEDED


def test_an_event_type_we_never_subscribed_to_is_ignored_not_failed():
    """Answering with a failure makes Stripe retry for days and eventually disable the endpoint,
    taking the events we DO care about with it."""
    body = _session_event(kind="invoice.updated")
    event = StripeWebhookVerifier(WEBHOOK_SECRET).verify(body, _sign(body))
    assert event.type == payment_event.IGNORED


def test_the_amount_that_reaches_the_books_is_the_one_stripe_reports():
    body = _session_event(amount_total=1999)
    event = StripeWebhookVerifier(WEBHOOK_SECRET).verify(body, _sign(body))
    assert event.payment.amount.to_usd() == pytest.approx(19.99)


# --- configuration -----------------------------------------------------------


def test_stripe_without_a_key_refuses_to_build(monkeypatch):
    """Better a service that will not start than one that opens checkouts it cannot charge."""
    monkeypatch.setenv("AGENTD_PAYMENT_PROVIDER", "stripe")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(PaymentConfigurationError, match="STRIPE_SECRET_KEY"):
        build_payment_gateway()


def test_the_webhook_is_only_mounted_for_a_rail_that_has_one(monkeypatch):
    monkeypatch.setenv("AGENTD_PAYMENT_PROVIDER", "null")
    assert has_webhook() is False
    monkeypatch.setenv("AGENTD_PAYMENT_PROVIDER", "stripe")
    assert has_webhook() is True


# --- end to end, against the real accounts service ---------------------------


@pytest.fixture
def stripe_accounts(monkeypatch, tmp_path):
    """The accounts service with the Stripe rail configured and its HTTP calls stubbed."""
    monkeypatch.setenv("AGENTD_ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("ACCOUNTS_RATE_LIMIT", "0/0")
    monkeypatch.setenv("ACCOUNTS_INTERNAL_KEY", "devinternal")
    monkeypatch.setenv("AGENTD_TELEMETRY", "0")
    monkeypatch.setenv("AGENTD_CREDITS_PER_USD", "166667")
    monkeypatch.setenv("AGENTD_CREDIT_MARKUP", "2.0")
    monkeypatch.setenv("AGENTD_PAYMENT_PROVIDER", "stripe")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)

    spec = importlib.util.spec_from_file_location("agentd_accounts_app_stripe", ACCOUNTS_APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    sessions: list[httpx.Request] = []

    def build(*_a, **_k):
        return StripePaymentGateway(
            StripeApiClient(
                "sk_test_x",
                transport=httpx.MockTransport(lambda r: (
                    sessions.append(r),
                    httpx.Response(200, json={
                        "id": "cs_test_1",
                        "url": "https://checkout.stripe.com/c/pay/cs_test_1",
                        "payment_status": "unpaid",
                    }),
                )[1]),
            )
        )

    monkeypatch.setattr(module, "build_payment_gateway", build)
    with TestClient(module.app) as client:
        client.post("/signup", json={"email": "buyer@x.io", "password": "password123"})
        d = client.post("/login", json={"email": "buyer@x.io", "password": "password123"}).json()
        yield client, d["account_id"], {"Authorization": f"Bearer {d['token']}"}, sessions


def test_a_card_checkout_grants_nothing_until_the_callback(stripe_accounts):
    """The whole point. The customer has been sent to Stripe; the balance has not moved."""
    client, _account_id, auth, _sessions = stripe_accounts
    before = client.get("/me/credits", headers=auth).json()["credits_remaining"]

    r = client.post("/me/checkout", headers=auth, json={
        "product_id": "credits-100k", "idempotency_key": "click-1",
        "success_url": "https://app.example/ok", "cancel_url": "https://app.example/no",
    })

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert r.json()["checkout_url"] == "https://checkout.stripe.com/c/pay/cs_test_1"
    assert client.get("/me/credits", headers=auth).json()["credits_remaining"] == before


def test_the_callback_grants_the_credits_exactly_once(stripe_accounts):
    client, account_id, auth, _sessions = stripe_accounts
    before = client.get("/me/credits", headers=auth).json()["credits_remaining"]
    started = client.post("/me/checkout", headers=auth, json={
        "product_id": "credits-100k", "idempotency_key": "click-1",
        "success_url": "https://app.example/ok", "cancel_url": "https://app.example/no",
    }).json()

    # The order Stripe hands back is the one we sent it, signed.
    order = client.get("/products", params={"kind": "credit_pack"}).json()["products"]
    pack = next(p for p in order if p["id"] == "credits-100k")
    body = _session_event(metadata={
        "account_id": account_id, "price_usd": f"{float(pack['price_usd']):.6f}",
        "credits": str(pack["credits"]), "scope": "platform", "tier_max": "",
        "period_days": "365", "creator_id": "", "agent_id": "", "product_id": "credits-100k",
    }, amount_total=round(float(pack["price_usd"]) * 100))

    signed = {"stripe-signature": _sign(body)}
    first = client.post("/payments/webhook", content=body, headers=signed)
    second = client.post("/payments/webhook", content=body, headers=signed)

    assert first.status_code == 200 and first.json()["processed"] is True
    assert second.json()["duplicate"] is True and second.json()["processed"] is False
    after = client.get("/me/credits", headers=auth).json()["credits_remaining"]
    assert after == before + int(pack["credits"])
    assert started["credits"] == int(pack["credits"])
    books = client.get("/ledger/balances", headers={"X-Internal-Key": "devinternal"})
    assert books.json()["balanced"]


def test_an_unsigned_callback_cannot_mint_credits(stripe_accounts):
    client, account_id, auth, _sessions = stripe_accounts
    before = client.get("/me/credits", headers=auth).json()["credits_remaining"]
    body = _session_event(metadata={
        "account_id": account_id, "price_usd": "600.000000", "credits": "100000",
        "scope": "platform", "tier_max": "", "period_days": "365",
        "creator_id": "", "agent_id": "", "product_id": "credits-100k",
    })

    r = client.post("/payments/webhook", content=body,
                    headers={"stripe-signature": _sign(body, secret="whsec_forged")})

    assert r.status_code == 400
    assert client.get("/me/credits", headers=auth).json()["credits_remaining"] == before


def test_a_callback_for_an_amount_we_never_quoted_is_refused(stripe_accounts):
    """Metadata says one price, the money says another. Granting either would be a guess about
    which of two disagreeing sources is right, on a question worth real money."""
    client, account_id, auth, _sessions = stripe_accounts
    body = _session_event(amount_total=100, metadata={
        "account_id": account_id, "price_usd": "600.000000", "credits": "100000",
        "scope": "platform", "tier_max": "", "period_days": "365",
        "creator_id": "", "agent_id": "", "product_id": "credits-100k",
    })
    with pytest.raises(ValueError, match="refusing to grant"):
        client.post("/payments/webhook", content=body, headers={"stripe-signature": _sign(body)})


def test_a_client_still_cannot_choose_its_own_price(stripe_accounts):
    """Same rule as /me/purchase: the products row is the only source of price and credits."""
    client, _account_id, auth, sessions = stripe_accounts
    client.post("/me/checkout", headers=auth, json={
        "product_id": "credits-1k", "usd": 0.01, "credits": 10_000_000,
        "success_url": "https://app.example/ok", "cancel_url": "https://app.example/no",
    })
    sent = dict(parse_qsl(sessions[-1].content.decode()))
    assert sent["metadata[credits]"] == "1000"
    assert sent["line_items[0][price_data][unit_amount]"] != "1"


def test_a_return_url_outside_the_allowlist_is_refused(stripe_accounts, monkeypatch):
    """Unconstrained, this is an open redirect wearing our domain in the address bar."""
    client, _account_id, auth, _sessions = stripe_accounts
    monkeypatch.setenv("AGENTD_CHECKOUT_RETURN_ORIGINS", "https://app.example")
    r = client.post("/me/checkout", headers=auth, json={
        "product_id": "credits-1k", "success_url": "https://evil.example/ok",
        "cancel_url": "https://app.example/no",
    })
    assert r.status_code == 400 and "allowed origin" in r.text
