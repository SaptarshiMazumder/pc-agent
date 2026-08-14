"""StripeWebhookVerifier — prove a delivery came from Stripe, then say what it means.

THIS IS THE SECURITY BOUNDARY OF THE WHOLE MODULE. The webhook endpoint is public and
unauthenticated — it has to be, the rail has no session with us — so this signature is the only
thing between a stranger and a free credit grant. Everything downstream — claim, record,
post-process — assumes it already ran.

THE SCHEME, so the reader does not have to trust a vendor SDK to know what is checked:

    Stripe-Signature: t=1699999999,v1=<hex>,v1=<hex during a secret rotation>
    signed payload  = f"{t}.{raw body}"
    expected        = HMAC-SHA256(webhook secret, signed payload)

TWO THINGS BEYOND THE HMAC:

  * the RAW BYTES are signed. Parsing to JSON and re-serialising changes key order and
    whitespace and the signature stops matching — which is why the HTTP layer must not let its
    framework parse the body first.
  * the TIMESTAMP is checked against a tolerance. Without it, a valid delivery captured once can
    be replayed forever; the signature stays good because the body never changes.

Comparison is constant-time. A timing oracle on a signature check is a real, published attack.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Callable

from payments.application.interfaces.webhook_verifier import WebhookRejected
from payments.domain import payment_event, payment_status
from payments.domain.money import Money
from payments.domain.payment_event import PaymentEvent
from payments.domain.payment_intent import PURCHASE, REFUND, PaymentIntent

DEFAULT_TOLERANCE_S = 300

#: Stripe's event names, mapped to ours. Anything absent is IGNORED — recorded, acted on by
#: nothing. Rails emit dozens of types nobody subscribed to, and failing those makes Stripe retry
#: them for days and eventually disable the endpoint, taking the events we DO care about with it.
EVENT_TYPES = {
    # The customer finished at the hosted page. `payment_status` still decides: a delayed method
    # (bank debit) completes the SESSION while the money is still in flight, and granting there
    # would hand over credits for a payment that can still fail days later.
    "checkout.session.completed": payment_event.PURCHASE_SUCCEEDED,
    "checkout.session.async_payment_succeeded": payment_event.PURCHASE_SUCCEEDED,
    "checkout.session.async_payment_failed": payment_event.PURCHASE_FAILED,
    "checkout.session.expired": payment_event.PURCHASE_FAILED,
    "charge.refunded": payment_event.REFUND_SUCCEEDED,
}


class StripeWebhookVerifier:
    def __init__(
        self,
        webhook_secret: str,
        *,
        tolerance_s: int = DEFAULT_TOLERANCE_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not webhook_secret:
            raise ValueError("a Stripe webhook signing secret is required")
        self._secret = webhook_secret
        self._tolerance_s = tolerance_s
        self._clock = clock

    def verify(self, body: bytes, signature: str) -> PaymentEvent:
        timestamp, candidates = self._parse(signature)
        expected = hmac.new(
            self._secret.encode("utf-8"),
            f"{timestamp}.".encode("utf-8") + body,
            hashlib.sha256,
        ).hexdigest()
        if not any(hmac.compare_digest(expected, given) for given in candidates):
            raise WebhookRejected("signature does not match")
        drift = abs(self._clock() - timestamp)
        if self._tolerance_s and drift > self._tolerance_s:
            raise WebhookRejected(f"timestamp is {int(drift)}s away; outside the replay window")
        return self._to_event(body)

    def _parse(self, signature: str) -> tuple[int, list[str]]:
        if not signature:
            raise WebhookRejected("no Stripe-Signature header")
        timestamp, candidates = 0, []
        for part in signature.split(","):
            key, _, value = part.strip().partition("=")
            if key == "t":
                try:
                    timestamp = int(value)
                except ValueError as e:
                    raise WebhookRejected("malformed timestamp") from e
            elif key == "v1":
                # More than one during a secret rotation — Stripe signs with both.
                candidates.append(value)
        if not timestamp or not candidates:
            raise WebhookRejected("malformed Stripe-Signature header")
        return timestamp, candidates

    def _to_event(self, body: bytes) -> PaymentEvent:
        try:
            envelope = json.loads(body)
        except ValueError as e:
            # Signed and unparseable means Stripe sent something we do not understand, not that
            # someone forged it. Still refuse: acting on a body we could not read is worse.
            raise WebhookRejected("signed body is not JSON") from e

        event_id = str(envelope.get("id") or "")
        if not event_id:
            raise WebhookRejected("event has no id; it could not be deduplicated")
        stripe_type = str(envelope.get("type") or "")
        obj = ((envelope.get("data") or {}).get("object")) or {}

        kind = EVENT_TYPES.get(stripe_type, payment_event.IGNORED)
        if kind == payment_event.PURCHASE_SUCCEEDED and stripe_type == "checkout.session.completed":
            if str(obj.get("payment_status") or "") != "paid":
                # Session finished, money has not arrived. `async_payment_succeeded` follows.
                kind = payment_event.IGNORED

        return PaymentEvent(id=event_id, type=kind, payment=self._to_intent(kind, obj))

    def _to_intent(self, kind: str, obj: dict) -> PaymentIntent:
        currency = str(obj.get("currency") or "usd")
        # `amount_total` on a session, `amount_refunded` on a charge. Neither is a float, and the
        # amount that reaches the books is the one the rail reports rather than the one we asked
        # for — those differ when a coupon or a currency conversion is involved.
        minor = obj.get("amount_total")
        if minor is None:
            minor = obj.get("amount_refunded", obj.get("amount", 0))
        status = {
            payment_event.PURCHASE_SUCCEEDED: payment_status.SUCCEEDED,
            payment_event.PURCHASE_FAILED: payment_status.FAILED,
            payment_event.REFUND_SUCCEEDED: payment_status.REFUNDED,
        }.get(kind, payment_status.PENDING)
        metadata = obj.get("metadata") or {}
        return PaymentIntent(
            kind=REFUND if kind == payment_event.REFUND_SUCCEEDED else PURCHASE,
            provider="stripe",
            reference=str(obj.get("id") or ""),
            amount=Money.from_minor_units(int(minor or 0), currency),
            status=status,
            account_id=str(obj.get("client_reference_id") or metadata.get("account_id") or ""),
            detail=str(obj.get("payment_status") or obj.get("status") or ""),
            meta={str(k): str(v) for k, v in metadata.items()},
        )
