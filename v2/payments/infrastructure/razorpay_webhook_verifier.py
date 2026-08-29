"""RazorpayWebhookVerifier — prove a delivery came from Razorpay, then say what it means.

THIS IS THE SECURITY BOUNDARY, same statement as the Stripe verifier: the endpoint is public and
unauthenticated, and this signature is the only thing between a stranger and a free credit grant.

THE SCHEME, simpler than Stripe's:

    x-razorpay-signature = hex( HMAC-SHA256(webhook secret, raw body) )

No timestamp is signed, so there is no replay window to check — Razorpay simply does not put one
in the scheme. What bounds a replay instead is the EVENT ID: Razorpay sends it in the
`x-razorpay-event-id` header (NOT in the body — the envelope has no id field, which is why this
verifier needs the headers at all), and PaymentEventService claims each id exactly once, so a
captured delivery replayed later answers "duplicate" and grants nothing. A delivery without that
header cannot be deduplicated and is refused outright.

THE RAW BYTES ARE SIGNED, same as every rail: the HTTP layer must not let its framework parse
the body first. Comparison is constant-time.

THE PAYLOAD IS A NAMED BAG, NOT ONE OBJECT. Stripe sends `data.object`; Razorpay sends
`payload.<entity name>.entity` for every entity the event `contains`. The one that matters
differs by event — and on `payment_link.paid` the intent's reference is deliberately the
PAYMENT id (pay_…), not the link id, because a refund is issued against the payment and the
webhook is the only moment we ever learn that id.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Mapping

from payments.application.interfaces.webhook_verifier import WebhookRejected
from payments.domain import payment_event, payment_status
from payments.domain.money import Money
from payments.domain.payment_event import PaymentEvent
from payments.domain.payment_intent import PURCHASE, REFUND, PaymentIntent

#: Razorpay's event names, mapped to ours. Anything absent is IGNORED — recorded, acted on by
#: nothing — for the same reason as on the Stripe rail: failing unsubscribed event types makes
#: the rail retry them and eventually disable the endpoint, taking the real ones with it.
EVENT_TYPES = {
    # `payment_link.paid` fires when the payment against the link is CAPTURED — Razorpay's
    # "the money is ours", so there is no paid-but-pending sub-state to re-check the way
    # Stripe's `payment_status` demands on session completion.
    "payment_link.paid": payment_event.PURCHASE_SUCCEEDED,
    "payment_link.expired": payment_event.PURCHASE_FAILED,
    "payment_link.cancelled": payment_event.PURCHASE_FAILED,
    "refund.processed": payment_event.REFUND_SUCCEEDED,
}


class RazorpayWebhookVerifier:
    def __init__(self, webhook_secret: str) -> None:
        if not webhook_secret:
            raise ValueError("a Razorpay webhook secret is required")
        self._secret = webhook_secret

    def verify(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent:
        signature = (headers.get("x-razorpay-signature") or "").strip()
        if not signature:
            raise WebhookRejected("no X-Razorpay-Signature header")
        expected = hmac.new(self._secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise WebhookRejected("signature does not match")
        event_id = (headers.get("x-razorpay-event-id") or "").strip()
        if not event_id:
            raise WebhookRejected("event has no id; it could not be deduplicated")
        return self._to_event(event_id, body)

    def _to_event(self, event_id: str, body: bytes) -> PaymentEvent:
        try:
            envelope = json.loads(body)
        except ValueError as e:
            # Signed and unparseable means Razorpay sent something we do not understand, not
            # that someone forged it. Still refuse: acting on a body we could not read is worse.
            raise WebhookRejected("signed body is not JSON") from e
        kind = EVENT_TYPES.get(str(envelope.get("event") or ""), payment_event.IGNORED)
        return PaymentEvent(id=event_id, type=kind, payment=self._to_intent(kind, envelope))

    def _to_intent(self, kind: str, envelope: dict) -> PaymentIntent:
        payload = envelope.get("payload") or {}

        def entity(name: str) -> dict:
            return ((payload.get(name) or {}).get("entity")) or {}

        payment, link, refund = entity("payment"), entity("payment_link"), entity("refund")
        # The link's notes are authoritative — they are what begin_purchase wrote — and the
        # payment inherits them; read the link's first so a rail-side propagation quirk cannot
        # lose the order.
        notes = link.get("notes") or payment.get("notes") or refund.get("notes") or {}
        meta = {str(k): str(v) for k, v in notes.items()}

        if kind == payment_event.REFUND_SUCCEEDED:
            primary, reference = refund, str(refund.get("id") or "")
            meta["original"] = str(refund.get("payment_id") or "")
        elif kind == payment_event.PURCHASE_SUCCEEDED:
            # The payment is the primary entity: its amount is what was actually captured, and
            # its id is what a refund needs. The link id is kept for reconciliation.
            primary, reference = payment, str(payment.get("id") or "")
            if link.get("id"):
                meta["payment_link"] = str(link["id"])
        else:
            primary = link or payment or refund
            reference = str(primary.get("id") or "")

        status = {
            payment_event.PURCHASE_SUCCEEDED: payment_status.SUCCEEDED,
            payment_event.PURCHASE_FAILED: payment_status.FAILED,
            payment_event.REFUND_SUCCEEDED: payment_status.REFUNDED,
        }.get(kind, payment_status.PENDING)

        # Integer minor units, like every rail; the amount that reaches the books is the one
        # the rail reports, not the one we asked for.
        return PaymentIntent(
            kind=REFUND if kind == payment_event.REFUND_SUCCEEDED else PURCHASE,
            provider="razorpay",
            reference=reference,
            amount=Money.from_minor_units(
                int(primary.get("amount") or 0), str(primary.get("currency") or "usd")
            ),
            status=status,
            account_id=str(notes.get("account_id") or ""),
            detail=str(primary.get("status") or ""),
            meta=meta,
        )
