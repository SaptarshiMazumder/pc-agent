"""StripePaymentGateway — Stripe Checkout, the hosted page.

WHY HOSTED AND NOT AN EMBEDDED FORM. The card number never touches our origin, which takes the
whole of PCI scope, 3-D Secure, wallets, receipts and localisation off us and puts them on
Stripe. The cost is a redirect away from the app. For a first rail that trade is not close.

SO A PURCHASE DOES NOT FINISH HERE. `begin_purchase` creates a Session and returns PENDING plus a
`redirect_url`. The money becomes real later, on a webhook, on a request nobody started — which
is precisely the shape `PaymentGateway` exists to express and the old `Charge(ok=...)` could not.

THE ORDER TRAVELS IN THE SESSION'S METADATA and comes back signed on the webhook. That is what
lets post-processing reconstruct what was bought without trusting anything the customer's browser
says, and without depending on the products row still holding the same price minutes later — the
customer buys what they were shown.

WHAT IS NOT BUILT: `charge_off_session`. A renewal needs a card on file, which needs a Stripe
Customer created and a payment method saved at first checkout. It RAISES rather than returning a
decline, because a decline is a fact about a customer and this is a fact about us — reporting our
missing feature as their card failing would send the wrong dunning email to a paying subscriber.
"""

from __future__ import annotations

from payments.application.interfaces.payment_gateway import (
    PaymentConfigurationError,
    PurchaseRequest,
)
from payments.domain import payment_status
from payments.domain.money import Money
from payments.domain.payment_intent import PAYOUT, PURCHASE, REFUND, PaymentIntent
from payments.infrastructure.stripe_api_client import StripeApiClient, StripeApiError


class StripePaymentGateway:
    name = "stripe"
    purchase_note = (
        "You will be taken to Stripe's secure payment page. Your card details never reach us."
    )

    def __init__(self, client: StripeApiClient, *, statement_descriptor: str = "") -> None:
        self._client = client
        self._statement_descriptor = statement_descriptor

    def begin_purchase(self, request: PurchaseRequest) -> PaymentIntent:
        if not request.amount.positive:
            raise ValueError("a purchase needs a positive amount")
        if not request.success_url or not request.cancel_url:
            raise PaymentConfigurationError(
                "Stripe Checkout needs success_url and cancel_url — it has to know where to "
                "return the customer"
            )
        payload = {
            "mode": "payment",
            "success_url": request.success_url,
            "cancel_url": request.cancel_url,
            # Stripe's own account correlation, visible in their dashboard. The metadata below is
            # what post-processing actually reads; this is for a human looking at a payment.
            "client_reference_id": request.account_id,
            "metadata": {"account_id": request.account_id, **_strings(request.meta)},
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": request.amount.currency,
                        "unit_amount": request.amount.minor_units(),
                        "product_data": {"name": request.description or "Credits"},
                    },
                }
            ],
        }
        if self._statement_descriptor:
            payload["payment_intent_data"] = {
                "statement_descriptor": self._statement_descriptor[:22]
            }

        try:
            session = self._client.post(
                "/v1/checkout/sessions", payload, idempotency_key=request.idempotency_key
            )
        except StripeApiError as e:
            # Stripe REFUSED TO OPEN a checkout — a bad key, an impossible amount. Nothing was
            # charged and nothing will be, so this is a settled failure rather than an outage,
            # and the caller can show the rail's own words.
            return PaymentIntent(
                kind=PURCHASE,
                provider=self.name,
                reference="",
                amount=request.amount,
                status=payment_status.FAILED,
                account_id=request.account_id,
                idempotency_key=request.idempotency_key,
                detail=str(e),
                meta={"code": e.code, "type": e.kind},
            )

        return PaymentIntent(
            kind=PURCHASE,
            provider=self.name,
            reference=str(session.get("id") or ""),
            amount=request.amount,
            status=payment_status.PENDING,
            account_id=request.account_id,
            idempotency_key=request.idempotency_key,
            redirect_url=str(session.get("url") or ""),
            detail=str(session.get("payment_status") or "unpaid"),
            meta=_strings(request.meta),
        )

    def charge_off_session(self, request: PurchaseRequest) -> PaymentIntent:
        raise PaymentConfigurationError(
            "the Stripe rail cannot charge a card on file yet — subscription renewals need a "
            "saved payment method, which is not implemented. Renewals will fail loudly until it "
            "is; they are not silently cancelled."
        )

    def refund(self, *, reference: str, amount: Money, idempotency_key: str = "") -> PaymentIntent:
        payload = {"payment_intent": reference, "amount": amount.minor_units()}
        refund = self._client.post("/v1/refunds", payload, idempotency_key=idempotency_key)
        return PaymentIntent(
            kind=REFUND,
            provider=self.name,
            reference=str(refund.get("id") or ""),
            amount=amount,
            status=payment_status.REFUNDED,
            idempotency_key=idempotency_key,
            detail=str(refund.get("status") or ""),
            meta={"original": reference},
        )

    def payout(self, *, creator_id: str, amount: Money, idempotency_key: str = "") -> PaymentIntent:
        raise PaymentConfigurationError(
            "paying creators out needs Stripe Connect — creator onboarding, identity "
            "verification and a bank account per creator. Accruals are recorded; nothing can be "
            "withdrawn yet."
        )


def _strings(meta: dict) -> dict:
    """Stripe stores metadata as strings. Converting here rather than at each call site means a
    value that arrives as an int comes back as one we can still parse, instead of as `"None"`."""
    return {str(k): "" if v is None else str(v) for k, v in (meta or {}).items()}
