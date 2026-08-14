"""Contract tests for the payment rail module (`v2/payments/`).

These pin the rules the rail must obey no matter which provider is configured — above all the one
the old `Charge(ok=True|False)` shape could not express: a payment that has STARTED and not
finished. Everything about the Stripe integration hangs off that state existing and being handled
without anyone branching on which rail is in play.

The module is imported normally: `v2/` is on sys.path (tests/conftest.py), and the accounts image
copies the package to /app/payments, so `import payments...` means the same thing in both places.
"""

from __future__ import annotations

import sqlite3

import pytest

from payments.application.interfaces.payment_gateway import (
    PaymentConfigurationError,
    PaymentGateway,
    PurchaseRequest,
)
from payments.application.interfaces.webhook_verifier import WebhookRejected
from payments.application.services.checkout_service import CheckoutService
from payments.application.services.payment_event_service import PaymentEventService
from payments.domain import payment_event, payment_status
from payments.domain.processed_payment import ProcessedPayment
from payments.domain.money import Money
from payments.domain.payment_event import PaymentEvent
from payments.domain.payment_intent import PURCHASE, PaymentIntent
from payments.infrastructure.null_payment_gateway import NullPaymentGateway
from payments.infrastructure.sqlite_payment_intent_store import SqlitePaymentIntentStore
from payments.main.payment_gateway_factory import build_payment_gateway


# --- doubles -----------------------------------------------------------------


class RecordingPostProcessor:
    """Counts deliveries. The number of times this is called IS the thing under test — one
    delivery per payment, no matter how many times the rail says so."""

    def __init__(self) -> None:
        self.calls: list[PaymentIntent] = []

    def process(self, payment: PaymentIntent) -> ProcessedPayment:
        self.calls.append(payment)
        return ProcessedPayment(
            reference=f"txn-{len(self.calls)}", created=True, detail={"split": {}}
        )


class RedirectingGateway:
    """A rail that behaves the way a card does: starts the payment, hands back somewhere to send
    the customer, and settles later. No real rail is needed to pin this behaviour."""

    name = "redirecting"
    purchase_note = "You will be taken to a payment page."

    def begin_purchase(self, request: PurchaseRequest) -> PaymentIntent:
        return PaymentIntent(
            kind=PURCHASE,
            provider=self.name,
            reference="cs_test_1",
            amount=request.amount,
            status=payment_status.PENDING,
            account_id=request.account_id,
            idempotency_key=request.idempotency_key,
            redirect_url="https://pay.example/cs_test_1",
        )

    def charge_off_session(self, request: PurchaseRequest) -> PaymentIntent:
        return self.begin_purchase(request)

    def refund(self, *, reference, amount, idempotency_key=""):  # pragma: no cover - unused
        raise NotImplementedError

    def payout(self, *, creator_id, amount, idempotency_key=""):  # pragma: no cover - unused
        raise NotImplementedError


class StubVerifier:
    def __init__(self, event: PaymentEvent | None) -> None:
        self._event = event

    def verify(self, body: bytes, signature: str) -> PaymentEvent:
        if self._event is None or signature != "good":
            raise WebhookRejected("bad signature")
        return self._event


@pytest.fixture
def store():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    SqlitePaymentIntentStore.create_schema(c)
    return c


def _request(**kw) -> PurchaseRequest:
    return PurchaseRequest(
        account_id=kw.pop("account_id", "acct-1"),
        amount=kw.pop("amount", Money.from_usd(20.0)),
        idempotency_key=kw.pop("idempotency_key", "click-1"),
        **kw,
    )


def _succeeded(**kw) -> PaymentIntent:
    return PaymentIntent(
        kind=PURCHASE,
        provider="null",
        reference=kw.pop("reference", "ref-1"),
        amount=kw.pop("amount", Money.from_usd(20.0)),
        status=payment_status.SUCCEEDED,
        **kw,
    )


# --- money -------------------------------------------------------------------


def test_an_amount_too_fine_to_charge_is_refused_rather_than_rounded():
    """Half a cent cannot be sent to any rail. Rounding it silently charges a price the customer
    was never shown, and the rounding is applied to the charge but NOT to the credits granted —
    so the books drift by design, a fraction at a time."""
    with pytest.raises(ValueError, match="minor units"):
        Money(5_000, "usd").minor_units()


def test_a_zero_decimal_currency_is_not_multiplied_by_a_hundred():
    """100 JPY is `100` at the rail, not `10000`. Assuming two decimals everywhere overcharges a
    Japanese customer a hundredfold, and nothing in the response would look wrong."""
    assert Money.from_usd(20.0).minor_units() == 2000
    assert Money(100 * 1_000_000, "jpy").minor_units() == 100
    assert Money.from_minor_units(100, "jpy").to_usd() == 100.0


def test_micros_are_the_same_unit_the_ledger_uses():
    """`ledger.usd_to_micros` and `Money.from_usd` must agree, or every purchase is posted for a
    slightly different amount than it was charged."""
    assert Money.from_usd(19.99).micros == int(round(19.99 * 1_000_000))


# --- the rail ----------------------------------------------------------------


def test_the_null_rail_satisfies_the_gateway_contract():
    assert isinstance(NullPaymentGateway(), PaymentGateway)


def test_a_replayed_key_yields_the_same_reference_across_processes():
    """The property the mock exists to model. The original derived it from the builtin `hash()`,
    which is salted per process — so the same key produced a different reference after every
    restart, and callers were being tested against behaviour no real rail has."""
    a = NullPaymentGateway().begin_purchase(_request(idempotency_key="same"))
    b = NullPaymentGateway().begin_purchase(_request(idempotency_key="same"))
    assert a.reference == b.reference == "null_ch_" + a.reference.split("_")[-1]
    assert a.reference != NullPaymentGateway().begin_purchase(
        _request(idempotency_key="other")
    ).reference


def test_a_non_positive_amount_fails_rather_than_charging_nothing_successfully():
    intent = NullPaymentGateway().begin_purchase(_request(amount=Money.from_usd(0)))
    assert intent.failed and intent.reference == ""


def test_an_unknown_status_cannot_be_constructed():
    """A rail growing a state we have no rule for must stop the request, not fall through to
    'not succeeded, therefore fine' — which is how a paying customer receives nothing."""
    with pytest.raises(ValueError, match="unknown payment status"):
        PaymentIntent(
            kind=PURCHASE,
            provider="null",
            reference="ref-1",
            amount=Money.from_usd(20.0),
            status="whatever",
        )


# --- checkout ----------------------------------------------------------------


def test_a_settled_purchase_is_delivered_in_the_same_request(store):
    """The behaviour the system had before the rail moved out, preserved exactly: a rail that
    needs no interaction delivers now. Waiting for a webhook that will never arrive would leave a
    paying customer with nothing."""
    done_by = RecordingPostProcessor()
    intent, done = CheckoutService(
        NullPaymentGateway(), SqlitePaymentIntentStore(store), done_by, clock=lambda: 1.0
    ).begin(_request())

    assert intent.succeeded and done is not None and done.created
    assert len(done_by.calls) == 1


def test_an_unsettled_purchase_is_recorded_but_never_delivered(store):
    """THE REASON THIS MODULE EXISTS. A card returns 'I have started, send the customer here'.
    Delivering then would hand over the credits before the money moved."""
    done_by = RecordingPostProcessor()
    intent, done = CheckoutService(
        RedirectingGateway(), SqlitePaymentIntentStore(store), done_by, clock=lambda: 1.0
    ).begin(_request())

    assert done is None and done_by.calls == []
    assert intent.awaiting_customer and intent.redirect_url
    assert not intent.failed, "pending is not a decline; the caller must be able to tell them apart"
    assert store.execute("SELECT COUNT(*) FROM payment_intents").fetchone()[0] == 1


def test_a_declined_payment_is_still_written_down(store):
    """A decline is evidence: it is what a support question is answered from and what a fraud
    pattern is spotted in. Recording only successes throws that away."""
    done_by = RecordingPostProcessor()
    _intent, done = CheckoutService(
        NullPaymentGateway(), SqlitePaymentIntentStore(store), done_by, clock=lambda: 1.0
    ).begin(_request(amount=Money.from_usd(0)))

    assert done is None and done_by.calls == []
    row = store.execute("SELECT status, amount_usd FROM payment_intents").fetchone()
    assert row["status"] == payment_status.FAILED


def test_a_renewal_takes_the_off_session_path():
    """Nobody is at a keyboard during a scheduled renewal, and a rail that wants to challenge the
    customer has to be told there is no customer to challenge."""
    seen: list[str] = []

    class Watching(RedirectingGateway):
        def begin_purchase(self, request):
            seen.append("interactive")
            return super().begin_purchase(request)

        def charge_off_session(self, request):
            seen.append("off_session")
            return super().begin_purchase(request)

    c = sqlite3.connect(":memory:")
    SqlitePaymentIntentStore.create_schema(c)
    service = CheckoutService(
        Watching(), SqlitePaymentIntentStore(c), RecordingPostProcessor(), clock=lambda: 1.0
    )
    service.begin(_request(), off_session=True)
    service.begin(_request(idempotency_key="click-2"))
    assert seen == ["off_session", "interactive"]


# --- webhooks ----------------------------------------------------------------


def test_an_unsigned_delivery_is_rejected_before_anything_is_read(store):
    """The endpoint is open to the internet. Until the signature checks out there is no event,
    only bytes a stranger posted — and a stranger must not be able to mint a credit grant."""
    done_by = RecordingPostProcessor()
    service = PaymentEventService(
        StubVerifier(None), SqlitePaymentIntentStore(store), done_by, clock=lambda: 1.0
    )
    with pytest.raises(WebhookRejected):
        service.handle(b"{}", "forged")
    assert done_by.calls == []
    assert store.execute("SELECT COUNT(*) FROM payment_events").fetchone()[0] == 0


def test_a_redelivered_event_is_processed_exactly_once(store):
    """Stripe retries a delivery for three days and does not guarantee order, so 'you were paid'
    arrives repeatedly. Post-processing twice grants the credits twice."""
    done_by = RecordingPostProcessor()
    event = PaymentEvent(id="evt_1", type=payment_event.PURCHASE_SUCCEEDED, payment=_succeeded())
    service = PaymentEventService(
        StubVerifier(event), SqlitePaymentIntentStore(store), done_by, clock=lambda: 1.0
    )

    first = service.handle(b"{}", "good")
    second = service.handle(b"{}", "good")

    assert first["processed"] and not second["processed"] and second["duplicate"]
    assert len(done_by.calls) == 1


def test_an_event_we_have_no_rule_for_is_recorded_and_ignored(store):
    """Rails emit dozens of event types nobody subscribed to. Answering with a failure makes the
    rail retry them forever and eventually disable the endpoint — taking the events we DO care
    about down with it."""
    done_by = RecordingPostProcessor()
    event = PaymentEvent(id="evt_2", type=payment_event.IGNORED, payment=_succeeded())
    result = PaymentEventService(
        StubVerifier(event), SqlitePaymentIntentStore(store), done_by, clock=lambda: 1.0
    ).handle(b"{}", "good")

    assert result["ok"] and not result["processed"] and done_by.calls == []
    assert store.execute("SELECT COUNT(*) FROM payment_intents").fetchone()[0] == 1


def test_claiming_an_event_is_a_write_not_a_read(store):
    """Two concurrent deliveries would both pass a read-then-write check and both deliver. The
    database decides, in one statement."""
    intents = SqlitePaymentIntentStore(store)
    assert intents.claim_event("evt_3", at=1.0) is True
    assert intents.claim_event("evt_3", at=1.0) is False


# --- configuration -----------------------------------------------------------


def test_an_unknown_provider_name_refuses_to_build(monkeypatch):
    """The money-printer guard. The version this replaced fell back to the mock rail on an
    unrecognised name, so `AGENTD_PAYMENT_PROVIDER=stipe` would make every checkout succeed,
    grant the credits, record a sale and take no money — with nothing anywhere looking wrong."""
    monkeypatch.setenv("AGENTD_PAYMENT_PROVIDER", "stipe")
    with pytest.raises(PaymentConfigurationError, match="unknown payment provider"):
        build_payment_gateway()


def test_the_default_is_the_mock_rail(monkeypatch):
    monkeypatch.delenv("AGENTD_PAYMENT_PROVIDER", raising=False)
    assert build_payment_gateway().name == "null"
