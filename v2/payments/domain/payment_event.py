"""PaymentEvent — what a rail told us, after the fact, over a webhook.

NORMALISED ON THE WAY IN. The `type` is one of the constants below, never the rail's own event
name, so the service that acts on it does not grow a branch per provider. A rail event we have no
rule for maps to `IGNORED` and is recorded without doing anything — the honest answer for
`invoice.updated`, and different from an event we failed to understand, which raises.

`id` IS THE RAIL'S EVENT ID, and it is load-bearing: Stripe retries a webhook for three days and
delivers out of order, so the same "you were paid" arrives many times. Post-processing twice grants
the credits twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from payments.domain.payment_intent import PaymentIntent

PURCHASE_SUCCEEDED = "purchase.succeeded"
PURCHASE_FAILED = "purchase.failed"
REFUND_SUCCEEDED = "refund.succeeded"
IGNORED = "ignored"

ALL_TYPES = frozenset({PURCHASE_SUCCEEDED, PURCHASE_FAILED, REFUND_SUCCEEDED, IGNORED})


@dataclass(frozen=True)
class PaymentEvent:
    id: str  # the rail's event id — the deduplication key
    type: str
    payment: PaymentIntent

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("a payment event needs the rail's event id; it is the dedupe key")
        if self.type not in ALL_TYPES:
            raise ValueError(f"unknown payment event type {self.type!r}")
