"""NullPaymentGateway — records intent, moves nothing. Lifted from `accounts/payments.py`.

WHAT "MOCKED" MEANS PRECISELY. Every purchase succeeds and returns a reference that is obviously
fake (`null_...`). It records an amount, a currency and a payer, and moves no money. Every
downstream consequence — the ledger posting, the credit grant, the creator's accrual — is real,
because money history cannot be backfilled. Nothing anywhere may branch on "is this the null
rail": if a code path only works because payments are fake, it is not built yet.

IT FINISHES IN ONE STEP, and that is the honest answer for a rail with no customer interaction —
so `CheckoutService` post-processes immediately, which is exactly the behaviour this system
had before the rail was pulled out into its own module.

ONE FIX ON THE WAY ACROSS. The original derived its reference with the builtin `hash()`, and
claimed a replay would yield the same reference "exactly as a real rail would". It would not:
`hash()` of a string is salted per process, so the same key produced a different reference after
every restart — the property the mock existed to model was the one it did not have. blake2b is
stable across processes and machines.
"""

from __future__ import annotations

import hashlib
import uuid

from payments.application.interfaces.payment_gateway import PurchaseRequest
from payments.domain import payment_status
from payments.domain.money import Money
from payments.domain.payment_intent import PAYOUT, PURCHASE, REFUND, PaymentIntent


class NullPaymentGateway:
    name = "null"
    purchase_note = (
        "Test mode: no card is charged and no money moves. Credits are added immediately."
    )

    def _ref(self, kind: str, idempotency_key: str) -> str:
        """Derived from the idempotency key, so replaying a request yields the SAME reference —
        the behaviour a real rail has, and therefore the behaviour callers must be tested
        against. An absent key gets a random one, which is the only honest answer: without a key
        there is nothing to replay."""
        seed = idempotency_key or uuid.uuid4().hex
        digest = hashlib.blake2b(seed.encode("utf-8"), digest_size=4).hexdigest()
        return f"null_{kind}_{digest}"

    def begin_purchase(self, request: PurchaseRequest) -> PaymentIntent:
        if not request.amount.positive:
            return PaymentIntent(
                kind=PURCHASE,
                provider=self.name,
                reference="",
                amount=request.amount,
                status=payment_status.FAILED,
                account_id=request.account_id,
                idempotency_key=request.idempotency_key,
                detail="amount must be positive",
                meta={"reason": "invalid_amount"},
            )
        return PaymentIntent(
            kind=PURCHASE,
            provider=self.name,
            reference=self._ref("ch", request.idempotency_key),
            amount=request.amount,
            status=payment_status.SUCCEEDED,
            account_id=request.account_id,
            idempotency_key=request.idempotency_key,
            detail="mocked: no money moved",
            meta={"description": request.description, **request.meta},
        )

    def charge_off_session(self, request: PurchaseRequest) -> PaymentIntent:
        """A renewal against a card that is not there. Identical to an interactive purchase here
        because nothing is charged either way; a real rail's two paths differ sharply."""
        return self.begin_purchase(request)

    def refund(self, *, reference: str, amount: Money, idempotency_key: str = "") -> PaymentIntent:
        return PaymentIntent(
            kind=REFUND,
            provider=self.name,
            reference=self._ref("re", idempotency_key),
            amount=amount,
            status=payment_status.REFUNDED,
            idempotency_key=idempotency_key,
            detail="mocked: no money moved",
            meta={"original": reference},
        )

    def payout(
        self, *, creator_id: str, amount: Money, idempotency_key: str = ""
    ) -> PaymentIntent:
        return PaymentIntent(
            kind=PAYOUT,
            provider=self.name,
            reference=self._ref("po", idempotency_key),
            amount=amount,
            status=payment_status.SUCCEEDED,
            idempotency_key=idempotency_key,
            detail="mocked: no money moved",
            meta={"creator_id": creator_id},
        )
