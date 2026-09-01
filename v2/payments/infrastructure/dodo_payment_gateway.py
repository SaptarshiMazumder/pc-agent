"""DodoPaymentGateway — Dodo Payments checkout sessions, the hosted page.

DODO IS A MERCHANT OF RECORD, and that is the reason to carry it as a rail at all: Dodo is the
legal seller, so global sales tax, VAT and GST are THEIR filing problem, not ours. The price of
that arrangement shapes this file in two ways.

FIRST: A CHARGE MUST NAME A PRODUCT IN THEIR CATALOG. There is no Stripe-style ad-hoc
`price_data` — an MoR sells its own catalog. So the deployment creates ONE product in the Dodo
dashboard, configured as PAY WHAT YOU WANT, and `DODO_PRODUCT_ID` names it; every purchase is
that product with the item's `amount` set to this purchase's price. The product's dashboard
currency must be the currency the books charge in (USD) — the amount is denominated in it, and
this code has no way to see or fix a mismatch, so it is a deploy-time contract, stated here and
in the factory.

SECOND: `payment_link: true` ON `POST /payments` IS DEPRECATED; checkout sessions
(`POST /checkouts`) are the current door. Same shape as the other rails regardless: the customer
is redirected to `checkout_url`, and the truth arrives on a webhook — PENDING here, credits
granted there, never both.

IDEMPOTENCY IS THE ONE THING THIS RAIL CANNOT PROMISE. Dodo documents no idempotency header and
a session has no unique caller reference to collide on — so a double-click can mint two payable
sessions where Stripe replays and Razorpay refuses. The caller's key still travels in metadata:
the ledger's own idempotency dedupes the GRANT, so two sessions can never pay out twice, but a
customer who completes both pages has genuinely paid twice and the second shows up in
reconciliation (same reference in `meta`, no matching grant) as a refund to issue. Stated
plainly because pretending otherwise is how it would be discovered in production.

NOT BUILT, loudly, same doctrine as the other rails: `charge_off_session` (Dodo's subscriptions
are their own MoR products managed on their side, not an API to charge a saved card against our
ledger's renewals) and `payout` (an MoR pays out to US; paying creators is our books' problem
and no rail API).
"""

from __future__ import annotations

from payments.application.interfaces.payment_gateway import (
    PaymentConfigurationError,
    PurchaseRequest,
)
from payments.domain import payment_status
from payments.domain.money import Money
from payments.domain.payment_intent import PURCHASE, REFUND, PaymentIntent
from payments.infrastructure.dodo_api_client import DodoApiClient, DodoApiError


class DodoPaymentGateway:
    name = "dodo"
    purchase_note = (
        "You will be taken to Dodo Payments' secure checkout. Dodo Payments is the merchant "
        "of record for this purchase; your card details never reach us."
    )

    def __init__(self, client: DodoApiClient, *, product_id: str) -> None:
        if not product_id:
            raise PaymentConfigurationError(
                "the Dodo rail needs DODO_PRODUCT_ID — a merchant of record can only sell "
                "from its catalog, so one pay-what-you-want product must exist there"
            )
        self._client = client
        self._product_id = product_id

    def begin_purchase(self, request: PurchaseRequest) -> PaymentIntent:
        if not request.amount.positive:
            raise ValueError("a purchase needs a positive amount")
        if not request.success_url:
            raise PaymentConfigurationError(
                "a Dodo checkout session needs success_url — `return_url` is where the "
                "customer lands after success AND failure; there is no separate cancel url"
            )
        payload = {
            "product_cart": [
                {
                    "product_id": self._product_id,
                    "quantity": 1,
                    # The pay-what-you-want amount: THIS purchase's price, in the minor unit of
                    # the product's dashboard currency (the deploy-time contract above).
                    "amount": request.amount.minor_units(),
                }
            ],
            "return_url": request.success_url,
            # The caller's idempotency key rides along even though the rail will not act on
            # it — it is what reconciliation joins a duplicate session back to its order by.
            "metadata": {
                "account_id": request.account_id,
                **({"idempotency_key": request.idempotency_key} if request.idempotency_key else {}),
                **_strings(request.meta),
            },
        }

        try:
            session = self._client.post("/checkouts", payload)
        except DodoApiError as e:
            # Dodo REFUSED TO OPEN a session — a bad key, a missing product. Nothing was
            # charged and nothing will be: a settled failure, in the rail's own words.
            return PaymentIntent(
                kind=PURCHASE,
                provider=self.name,
                reference="",
                amount=request.amount,
                status=payment_status.FAILED,
                account_id=request.account_id,
                idempotency_key=request.idempotency_key,
                detail=str(e),
                meta={"code": e.code},
            )

        return PaymentIntent(
            kind=PURCHASE,
            provider=self.name,
            reference=str(session.get("session_id") or ""),
            amount=request.amount,
            status=payment_status.PENDING,
            account_id=request.account_id,
            idempotency_key=request.idempotency_key,
            redirect_url=str(session.get("checkout_url") or ""),
            detail="session created",
            meta=_strings(request.meta),
        )

    def charge_off_session(self, request: PurchaseRequest) -> PaymentIntent:
        raise PaymentConfigurationError(
            "the Dodo rail cannot charge a saved instrument — Dodo's recurring billing is its "
            "own subscription products managed on their side, not an API against our renewals. "
            "Renewals will fail loudly until a rail that can do this is configured; they are "
            "not silently cancelled."
        )

    def refund(self, *, reference: str, amount: Money, idempotency_key: str = "") -> PaymentIntent:
        # `reference` is the PAYMENT id (from the webhook, where we first learn it) — a session
        # id cannot be refunded. Dodo's partial refunds are per-item; this deployment sells one
        # item per session, so a full refund of the payment is the only shape issued here.
        payload: dict = {"payment_id": reference}
        if idempotency_key:
            payload["metadata"] = {"idempotency_key": idempotency_key}
        refund = self._client.post("/refunds", payload)
        return PaymentIntent(
            kind=REFUND,
            provider=self.name,
            reference=str(refund.get("refund_id") or ""),
            amount=amount,
            status=payment_status.REFUNDED,
            idempotency_key=idempotency_key,
            detail=str(refund.get("status") or ""),
            meta={"original": reference},
        )

    def payout(self, *, creator_id: str, amount: Money, idempotency_key: str = "") -> PaymentIntent:
        raise PaymentConfigurationError(
            "a merchant of record pays out to us, not to our creators — creator payouts are "
            "not a Dodo API. Accruals are recorded; nothing can be withdrawn through this rail."
        )


def _strings(meta: dict) -> dict:
    """Dodo metadata accepts several scalar types; sending strings keeps what comes back on the
    webhook identical across all three rails, so post-processing parses one shape."""
    return {str(k): "" if v is None else str(v) for k, v in (meta or {}).items()}
