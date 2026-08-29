"""RazorpayPaymentGateway — Razorpay Payment Links, the hosted page.

WHY PAYMENT LINKS AND NOT THE JS CHECKOUT. Razorpay's popular integration is a script the page
embeds; it puts our origin in the payment path and a client integration in every surface that
sells. A Payment Link is Razorpay's own hosted page behind one server-side POST — the customer
is redirected away, pays there, and the truth arrives on a webhook. That is the same shape as
Stripe Checkout, so the client contract ("follow `checkout_url` when present") holds unchanged.

SO A PURCHASE DOES NOT FINISH HERE. `begin_purchase` creates a link and returns PENDING plus its
`short_url`. Credits are granted by the webhook (`payment_link.paid`), never by this call.

THE ORDER TRAVELS IN THE LINK'S `notes` — Razorpay's metadata, string values — and comes back
signed on the webhook, for the same reason Stripe uses session metadata: post-processing rebuilds
what was bought without trusting the browser or the products row as it stands minutes later.

IDEMPOTENCY IS `reference_id`, NOT A HEADER. Razorpay has no replay-the-response header; what it
has is a uniqueness rule — a second link with the same `reference_id` is refused. So the caller's
key becomes the reference (hashed down when longer than Razorpay's 40-character cap), and when a
create is refused AND a link already exists under that reference, the existing link IS the
replay: the double-click gets the first click's page. A refusal with no such link stays a
refusal, reported in the rail's own words — the lookup narrows the error, it never swallows one.

WHAT IS NOT BUILT, on purpose and loudly — the same two holes as the Stripe rail, for the same
reasons: `charge_off_session` (renewals need Razorpay's tokenised recurring flow — a mandate the
customer approves at first purchase, which no checkout here creates yet) and `payout` (RazorpayX
is its own product and its own onboarding).
"""

from __future__ import annotations

import hashlib

from payments.application.interfaces.payment_gateway import (
    PaymentConfigurationError,
    PurchaseRequest,
)
from payments.domain import payment_status
from payments.domain.money import Money
from payments.domain.payment_intent import PURCHASE, REFUND, PaymentIntent
from payments.infrastructure.razorpay_api_client import RazorpayApiClient, RazorpayApiError

#: Razorpay refuses a reference_id longer than this; longer caller keys are hashed down to fit.
REFERENCE_ID_MAX = 40


class RazorpayPaymentGateway:
    name = "razorpay"
    purchase_note = (
        "You will be taken to Razorpay's secure payment page. Your card details never reach us."
    )

    def __init__(self, client: RazorpayApiClient) -> None:
        self._client = client

    def begin_purchase(self, request: PurchaseRequest) -> PaymentIntent:
        if not request.amount.positive:
            raise ValueError("a purchase needs a positive amount")
        if not request.success_url:
            raise PaymentConfigurationError(
                "a Razorpay Payment Link needs success_url — it has to know where to return "
                "the customer (Razorpay has no separate cancel return; an abandoned link "
                "simply expires)"
            )
        reference = _reference_id(request.idempotency_key)
        payload = {
            "amount": request.amount.minor_units(),
            "currency": request.amount.currency.upper(),
            "description": request.description or "Credits",
            "callback_url": request.success_url,
            "callback_method": "get",
            "notes": {"account_id": request.account_id, **_strings(request.meta)},
        }
        if reference:
            payload["reference_id"] = reference

        try:
            link = self._client.post("/v1/payment_links", payload)
        except RazorpayApiError as e:
            # Refused. If a link already exists under this reference, the refusal IS the
            # uniqueness rule doing its job, and the first attempt's link is the idempotent
            # replay. Anything else — bad key, impossible amount, or a failed lookup — settles
            # as FAILED carrying the CREATE error: nothing was charged and nothing will be.
            existing = self._existing_link(reference)
            if existing is None:
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
            link = existing

        return PaymentIntent(
            kind=PURCHASE,
            provider=self.name,
            reference=str(link.get("id") or ""),
            amount=request.amount,
            status=payment_status.PENDING,
            account_id=request.account_id,
            idempotency_key=request.idempotency_key,
            redirect_url=str(link.get("short_url") or ""),
            detail=str(link.get("status") or "created"),
            meta=_strings(request.meta),
        )

    def charge_off_session(self, request: PurchaseRequest) -> PaymentIntent:
        raise PaymentConfigurationError(
            "the Razorpay rail cannot charge a saved instrument yet — renewals need Razorpay's "
            "recurring-payments mandate, which no checkout here sets up. Renewals will fail "
            "loudly until it is built; they are not silently cancelled."
        )

    def refund(self, *, reference: str, amount: Money, idempotency_key: str = "") -> PaymentIntent:
        # `reference` is the PAYMENT id (pay_…) — the webhook verifier put it on the succeeded
        # intent for exactly this call; a Payment Link id cannot be refunded.
        payload = {"amount": amount.minor_units()}
        if idempotency_key:
            payload["notes"] = {"idempotency_key": idempotency_key}
        refund = self._client.post(f"/v1/payments/{reference}/refund", payload)
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
            "paying creators out needs RazorpayX — its own onboarding, KYC and a fund account "
            "per creator. Accruals are recorded; nothing can be withdrawn yet."
        )

    def _existing_link(self, reference: str) -> dict | None:
        """The link an earlier attempt with this reference created, or None.

        None on a lookup FAILURE too — deliberately: the caller then reports the original
        create error, which is the actionable one. A missing replay degrades a double-click
        into a visible refusal, never into a second charge.
        """
        if not reference:
            return None
        try:
            found = self._client.get("/v1/payment_links", {"reference_id": reference})
        except RazorpayApiError:
            return None
        links = found.get("payment_links") or []
        return links[0] if links else None


def _reference_id(idempotency_key: str) -> str:
    """The caller's key, made to fit Razorpay's cap without losing uniqueness.

    Callers send keys up to 120 characters (the accounts service caps client keys there);
    truncating one would make two DIFFERENT purchases collide on the uniqueness rule, so a long
    key is hashed instead — deterministic per key, so retries still meet their original link.
    """
    key = (idempotency_key or "").strip()
    if len(key) <= REFERENCE_ID_MAX:
        return key
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:REFERENCE_ID_MAX]


def _strings(meta: dict) -> dict:
    """Razorpay stores notes as strings, like Stripe's metadata. Converting here means a value
    that arrives as an int comes back as one we can still parse, instead of as `"None"`."""
    return {str(k): "" if v is None else str(v) for k, v in (meta or {}).items()}
