"""Contract tests for refunds — the money going back, and the credits going with it.

WHAT THESE PIN, and why each one exists rather than being obvious:

  * the books balance for ANY pair of (amount refunded, credits removed). Those two numbers are
    independent — the amount carries margin the reserve never held — so a refund that only
    balanced when they happened to be equal would pass a full-refund test and corrupt the books
    on every partial one.
  * the processing fee is NOT reversed. The rail keeps its fee on a refund, so reversing it here
    would invent money nobody returned; a purchase plus its full refund must leave us out
    exactly the fee.
  * credits come back in the SAME PROPORTION as the money. Refunding half the payment removes
    half the credits, read from what the purchase actually sold rather than recomputed from
    today's markup.
  * a balance is never driven negative. Someone can spend between asking for a refund and it
    being processed; the shortfall is recorded, not charged to them.
  * a redelivered refund posts once. Rails retry, and a second posting would take the credits
    twice.

THE ONE THAT WAS ACTUALLY BROKEN: `refund.processed` arrived, was recorded, and did nothing —
the event service dropped everything that was not a purchase. The money went back and the
credits stayed spendable. `test_refund_event_reaches_the_post_processor` is that regression.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

V2 = Path(__file__).resolve().parents[2]


def _load(path: Path, name: str):
    """Load a standalone service file by path. Both services are directories rather than
    importable packages, so this is the same approach test_ledger.py and test_metering.py take.

    REGISTERED IN sys.modules BEFORE EXECUTION, which the older loaders did not need to do.
    `@dataclass` resolves its annotations through `sys.modules[cls.__module__]`, so a module
    that defines one and is not registered fails during exec with a bare
    "'NoneType' object has no attribute '__dict__'" — pointing at dataclasses.py rather than at
    the file actually being loaded.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def led(monkeypatch):
    monkeypatch.setenv("AGENTD_CREDITS_PER_USD", "166667")
    monkeypatch.setenv("AGENTD_CREDIT_MARKUP", "2.0")
    monkeypatch.setenv("AGENTD_PROCESSING_FEE_PCT", "0.03")
    monkeypatch.setenv("AGENTD_CREATOR_SHARE_PCT", "0.80")
    monkeypatch.delenv("AGENTD_ALLOW_NEGATIVE_MARGIN", raising=False)
    return _load(V2 / "accounts" / "ledger.py", "agentd_ledger_refund")


@pytest.fixture
def book(led):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    led.schema(c)
    return c


# ── the accounting ────────────────────────────────────────────────────────────


def test_full_refund_leaves_us_out_exactly_the_processing_fee(book, led):
    """A purchase and its complete reversal. Cash should be down by the fee and nothing else —
    that fee is real money the rail kept, and pretending otherwise would invent it."""
    credits = led.credits_for_usd(20.0)
    gross = led.usd_to_micros(20.0)
    led.post_purchase(book, 1.0, account_id="a1", gross_micros=gross, credits_sold=credits)

    _txn, created = led.post_refund(
        book, 5.0, account_id="a1", refund_micros=gross, credits_removed=credits,
        ref="pay_1", idempotency_key="refund:rfnd_1",
    )
    assert created

    b = led.balances(book)
    assert b["balanced"], b
    # 3% of $20. The fee stayed an expense; the cash that paid it never came back.
    assert b["accounts"]["cash"] == pytest.approx(-0.60, abs=0.01)
    assert b["accounts"]["processing_fees"] == pytest.approx(0.60, abs=0.01)
    # The obligation is gone and so is the inference fenced off against it.
    assert b["accounts"]["user_credit_liability"] == pytest.approx(0.0, abs=0.01)
    assert b["accounts"]["inference_reserve"] == pytest.approx(0.0, abs=0.01)


def test_partial_refund_balances_although_amount_and_reserve_differ(book, led):
    """THE CASE A FULL-REFUND TEST CANNOT CATCH. Half the money back is $10; the reserve that
    half the credits held is $5. Two different numbers in one transaction — if the posting
    balanced only by their being equal, this is where it would break."""
    credits = led.credits_for_usd(20.0)
    led.post_purchase(book, 1.0, account_id="a1", gross_micros=led.usd_to_micros(20.0),
                      credits_sold=credits)

    _txn, created = led.post_refund(
        book, 5.0, account_id="a1",
        refund_micros=led.usd_to_micros(10.0), credits_removed=credits // 2,
        ref="pay_1", idempotency_key="refund:rfnd_half",
    )
    assert created

    b = led.balances(book)
    assert b["balanced"], b
    assert b["accounts"]["inference_reserve"] == pytest.approx(5.0, abs=0.01), \
        "half the credits are gone, so half the reserve is released and half still stands"


def test_a_redelivered_refund_posts_once(book, led):
    """Rails retry. Posting twice would take the credits twice and double the cash out."""
    credits = led.credits_for_usd(20.0)
    gross = led.usd_to_micros(20.0)
    led.post_purchase(book, 1.0, account_id="a1", gross_micros=gross, credits_sold=credits)

    first, created_1 = led.post_refund(book, 5.0, account_id="a1", refund_micros=gross,
                                       credits_removed=credits, idempotency_key="refund:rfnd_1")
    second, created_2 = led.post_refund(book, 6.0, account_id="a1", refund_micros=gross,
                                        credits_removed=credits, idempotency_key="refund:rfnd_1")
    assert created_1 and not created_2
    assert first == second
    assert led.balances(book)["accounts"]["cash"] == pytest.approx(-0.60, abs=0.01)


def test_a_zero_refund_writes_nothing(book, led):
    """Not an error — rails send zero-value events. It must simply not post."""
    txn, created = led.post_refund(book, 5.0, account_id="a1", refund_micros=0, credits_removed=0)
    assert (txn, created) == ("", False)


# ── taking the credits back ───────────────────────────────────────────────────


class _Ledger:
    """The real ledger module, which the post processor takes by injection."""

    def __init__(self, module):
        self._m = module
        self.calls: list[dict] = []

    def usd_to_micros(self, usd):
        return self._m.usd_to_micros(usd)

    def post_refund(self, c, ts, **kw):
        self.calls.append(kw)
        return self._m.post_refund(c, ts, **kw)


class _Money:
    def __init__(self, usd: float) -> None:
        self._usd = usd

    def to_usd(self) -> float:
        return self._usd


class _Payment:
    def __init__(self, usd: float, *, original: str, reference: str = "rfnd_1") -> None:
        self.amount = _Money(usd)
        self.reference = reference
        self.meta = {"original": original}


@pytest.fixture
def processor(book, led):
    """A post processor over a book that already holds one purchase and its grant."""
    module = _load(V2 / "accounts" / "accounts_post_processor.py", "agentd_post_processor")
    book.execute(
        "CREATE TABLE credit_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, "
        "org_id TEXT DEFAULT '', scope TEXT DEFAULT 'platform', credits INTEGER, "
        "credits_used INTEGER DEFAULT 0, credit_class TEXT DEFAULT 'paid', "
        "model_tier_max TEXT DEFAULT '', expires_at REAL DEFAULT 0, created_at REAL)"
    )
    credits = led.credits_for_usd(20.0)
    led.post_purchase(book, 1.0, account_id="a1", gross_micros=led.usd_to_micros(20.0),
                      credits_sold=credits, ref="pay_1")
    book.execute(
        "INSERT INTO credit_grants (account_id, credits, credits_used, expires_at, created_at) "
        "VALUES ('a1', ?, 0, 0, 1.0)", (credits,)
    )
    ledger = _Ledger(led)
    return module.WebhookPostProcessor(book, ledger, now=lambda: 5.0), book, led, credits, ledger


def test_credits_come_back_in_the_same_proportion_as_the_money(processor):
    """Half the money refunded removes half the credits — read from what the purchase sold."""
    proc, book, _led, credits, _l = processor
    done = proc.refund(_Payment(10.0, original="pay_1"))

    assert done.created
    assert done.detail["credits_removed"] == pytest.approx(credits // 2, abs=1)
    assert done.detail["credits_shortfall"] == 0
    left = book.execute(
        "SELECT credits - credits_used AS n FROM credit_grants WHERE account_id='a1'"
    ).fetchone()["n"]
    assert left == pytest.approx(credits - credits // 2, abs=1)


def test_a_balance_is_never_driven_negative(processor):
    """Spent between asking and processing. Take what is there, record the rest, and leave the
    account able to work — a hidden debt would refuse their next turn with no explanation."""
    proc, book, _led, credits, _l = processor
    book.execute("UPDATE credit_grants SET credits_used = ? WHERE account_id='a1'",
                 (credits - 100,))

    done = proc.refund(_Payment(20.0, original="pay_1"))

    assert done.detail["credits_removed"] == 100
    assert done.detail["credits_shortfall"] == credits - 100
    left = book.execute(
        "SELECT credits - credits_used AS n FROM credit_grants WHERE account_id='a1'"
    ).fetchone()["n"]
    assert left == 0, "emptied, never negative"


def test_a_refund_against_an_unknown_payment_raises(processor):
    """The webhook must 500 so the rail retries and alerts. Silently succeeding would leave
    money returned and credits kept, with nothing anywhere saying so."""
    proc, _book, _led, _credits, _l = processor
    with pytest.raises(ValueError, match="bought nothing here"):
        proc.refund(_Payment(20.0, original="pay_does_not_exist"))


def test_a_refund_larger_than_the_purchase_raises(processor):
    proc, _book, _led, _credits, _l = processor
    with pytest.raises(ValueError, match="exceeds"):
        proc.refund(_Payment(50.0, original="pay_1"))


# ── the regression ────────────────────────────────────────────────────────────


def test_refund_event_reaches_the_post_processor():
    """THE BUG THIS FEATURE IS. `PaymentEventService.handle` dropped every event that was not a
    purchase, so `refund.processed` was recorded and ignored: the rail sent the money back and
    the customer kept a spendable balance. Nothing logged it, because nothing had gone wrong as
    far as the service was concerned."""
    from payments.application.services.payment_event_service import PaymentEventService
    from payments.domain import payment_event

    class _Payload:
        # The service logs the rail's reference for every event it acts on, so the fake needs
        # one -- a bare object() passed until that logging existed and would pass again the
        # moment someone removed it, which is exactly the regression this file is here to stop.
        reference = "rfnd_1"

    class _Event:
        id = "evt_1"
        type = payment_event.REFUND_SUCCEEDED
        payment = _Payload()

    class _Verifier:
        def verify(self, body, headers):
            return _Event()

    class _Intents:
        def claim_event(self, event_id, *, at):
            return True

        def record(self, payment, *, at):
            pass

    class _Processor:
        def __init__(self):
            self.refunded = False

        def process(self, payment):
            raise AssertionError("a refund must not be treated as a purchase")

        def refund(self, payment):
            self.refunded = True
            return type("R", (), {"reference": "txn_1", "created": True})()

    processor = _Processor()
    out = PaymentEventService(_Verifier(), _Intents(), processor, clock=lambda: 1.0).handle(
        b"{}", {}
    )

    assert processor.refunded, "refund.processed reached nothing"
    assert out["processed"] is True
    assert out["type"] == payment_event.REFUND_SUCCEEDED
