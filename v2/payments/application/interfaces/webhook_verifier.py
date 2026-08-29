"""WebhookVerifier — turn an unauthenticated POST from the internet into a trusted event.

THE ENDPOINT IS PUBLIC AND UNAUTHENTICATED. It has to be: the rail calls it, and the rail has no
session with us. The signature is the ONLY thing standing between a stranger and a free credit
grant, so verification is not a step in handling the request — it is the thing that decides
whether there is a request at all.

IT VERIFIES BYTES, NOT A PARSED BODY. The signature covers the exact octets that were sent.
Parsing to JSON and re-serialising changes key order and whitespace and the signature stops
matching, so the caller must hand over the raw body — which means the HTTP layer must NOT let its
framework parse it first.

IT RECEIVES THE HEADERS, NOT A SIGNATURE STRING, because which headers carry the proof is rail
knowledge: Stripe sends one (`Stripe-Signature`), Razorpay one under another name plus the event
id in a third, and the Standard-Webhooks rails (Dodo) need three (`webhook-id`,
`webhook-timestamp`, `webhook-signature`). Extracting them in the HTTP layer would re-derive that
knowledge per rail in the wrong place; the verifier takes the whole mapping and reads its own.
Keys are LOWERCASE — the router normalises once so no verifier has to guess at casing.
"""

from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from payments.domain.payment_event import PaymentEvent


class WebhookRejected(Exception):
    """The delivery is not from the rail, or not intact. Answer 400 and do nothing else.

    Never fall through to processing an unverified payload, and never log its contents — a
    rejected delivery is the shape an attack takes.
    """


@runtime_checkable
class WebhookVerifier(Protocol):
    def verify(self, body: bytes, headers: Mapping[str, str]) -> PaymentEvent:
        """Raises `WebhookRejected` on a bad or missing signature. Header keys are lowercase."""
        ...
