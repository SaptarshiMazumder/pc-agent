"""DodoWebhookVerifier — prove a delivery came from Dodo Payments, then say what it means.

THIS IS THE SECURITY BOUNDARY, same statement as the other two verifiers: the endpoint is
public and unauthenticated, and this signature is the only thing between a stranger and a free
credit grant.

THE SCHEME IS STANDARD WEBHOOKS (the spec Dodo implements), and it is the reason the verifier
interface takes the whole header mapping — three headers participate:

    webhook-id          unique per event; the dedup key
    webhook-timestamp   unix seconds; bounds replay
    webhook-signature   space-separated list of "v1,<base64>" entries (several during a
                        secret rotation)

    signed payload = f"{webhook-id}.{webhook-timestamp}.{raw body}"
    expected       = base64( HMAC-SHA256(key, signed payload) )

THE KEY IS THE SECRET DECODED, NOT ITS RAW TEXT. Standard Webhooks secrets are base64 behind a
`whsec_` prefix, and every conforming library HMACs with the DECODED bytes — using the printed
string as the key produces signatures that never match, which presents exactly like a wrong
secret. Decoding happens in the constructor so a malformed secret fails at boot, not on the
first paying customer.

The id and timestamp are covered by the signature, so a forger can change neither; the
timestamp is additionally checked against a tolerance so a captured delivery cannot be replayed
outside the window, and inside it the claimed `webhook-id` answers "duplicate" and grants
nothing. Comparison is constant-time.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Callable, Mapping

from payments.application.interfaces.webhook_verifier import WebhookRejected
from payments.domain import payment_event, payment_status
from payments.domain.money import Money
from payments.domain.payment_event import PaymentEvent
from payments.domain.payment_intent import PURCHASE, REFUND, PaymentIntent

DEFAULT_TOLERANCE_S = 300

#: Dodo's event names, mapped to ours. Anything absent is IGNORED — recorded, acted on by
#: nothing — for the reason every rail shares: failing unsubscribed types makes the rail retry
#: and eventually disable the endpoint. `payment.processing` is deliberately absent: it is not
#: terminal, and `payment.succeeded` follows. `refund.failed` is likewise recorded-only — the
#: books changed nothing when the refund did not happen.
EVENT_TYPES = {
    "payment.succeeded": payment_event.PURCHASE_SUCCEEDED,
    "payment.failed": payment_event.PURCHASE_FAILED,
    "payment.cancelled": payment_event.PURCHASE_FAILED,
    "refund.succeeded": payment_event.REFUND_SUCCEEDED,
}


class DodoWebhookVerifier:
    def __init__(
        self,
        webhook_secret: str,
        *,
        tolerance_s: int = DEFAULT_TOLERANCE_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not webhook_secret:
            raise ValueError("a Dodo Payments webhook secret is required")
        encoded = webhook_secret.strip()
        if encoded.startswith("whsec_"):
            encoded = encoded[len("whsec_") :]
        try:
            self._key = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError(
                "the Dodo webhook secret is not valid base64 — copy it verbatim from the "
                "dashboard, whsec_ prefix and all"
            ) from e
        self._tolerance_s = tolerance_s
        self._clock = clock

    def verify(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent:
        event_id = (headers.get("webhook-id") or "").strip()
        timestamp_raw = (headers.get("webhook-timestamp") or "").strip()
        signature_header = (headers.get("webhook-signature") or "").strip()
        if not event_id or not timestamp_raw or not signature_header:
            raise WebhookRejected("missing webhook-id, webhook-timestamp or webhook-signature")
        try:
            timestamp = int(timestamp_raw)
        except ValueError as e:
            raise WebhookRejected("malformed webhook-timestamp") from e

        signed = f"{event_id}.{timestamp}.".encode("utf-8") + body
        expected = base64.b64encode(hmac.new(self._key, signed, hashlib.sha256).digest()).decode()
        candidates = [
            part.split(",", 1)[1]
            for part in signature_header.split(" ")
            if part.startswith("v1,")
        ]
        if not candidates:
            raise WebhookRejected("no v1 signature in webhook-signature header")
        if not any(hmac.compare_digest(expected, given) for given in candidates):
            raise WebhookRejected("signature does not match")
        drift = abs(self._clock() - timestamp)
        if self._tolerance_s and drift > self._tolerance_s:
            raise WebhookRejected(f"timestamp is {int(drift)}s away; outside the replay window")
        return self._to_event(event_id, body)

    def _to_event(self, event_id: str, body: bytes) -> PaymentEvent:
        try:
            envelope = json.loads(body)
        except ValueError as e:
            # Signed and unparseable means Dodo sent something we do not understand, not that
            # someone forged it. Still refuse: acting on a body we could not read is worse.
            raise WebhookRejected("signed body is not JSON") from e
        kind = EVENT_TYPES.get(str(envelope.get("type") or ""), payment_event.IGNORED)
        return PaymentEvent(
            id=event_id, type=kind, payment=self._to_intent(kind, envelope.get("data") or {})
        )

    def _to_intent(self, kind: str, data: dict) -> PaymentIntent:
        metadata = data.get("metadata") or {}
        meta = {str(k): str(v) for k, v in metadata.items()}
        if kind == payment_event.REFUND_SUCCEEDED:
            # `amount` on a refund, `total_amount` on a payment — and the refund keeps the
            # payment it reverses in meta, the join reconciliation works from.
            reference = str(data.get("refund_id") or "")
            minor = data.get("amount", 0)
            meta["original"] = str(data.get("payment_id") or "")
        else:
            # The payment id is what a later refund needs; it first exists HERE, on the
            # webhook — begin_purchase only ever saw a session id.
            reference = str(data.get("payment_id") or "")
            minor = data.get("total_amount", data.get("amount", 0))
        status = {
            payment_event.PURCHASE_SUCCEEDED: payment_status.SUCCEEDED,
            payment_event.PURCHASE_FAILED: payment_status.FAILED,
            payment_event.REFUND_SUCCEEDED: payment_status.REFUNDED,
        }.get(kind, payment_status.PENDING)
        return PaymentIntent(
            kind=REFUND if kind == payment_event.REFUND_SUCCEEDED else PURCHASE,
            provider="dodo",
            reference=reference,
            amount=Money.from_minor_units(
                int(minor or 0), str(data.get("currency") or "usd")
            ),
            status=status,
            account_id=str(metadata.get("account_id") or ""),
            detail=str(data.get("status") or ""),
            meta=meta,
        )
