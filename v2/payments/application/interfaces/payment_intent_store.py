"""PaymentIntentStore — the record of what we asked a third party to do, and what they told us.

TWO JOBS, and the second is not obvious:

  ``record``       write down every attempt, successful or not. This is the half of
                   reconciliation that is ours; the rail keeps the other half.
  ``claim_event``  the deduplication gate for webhooks. Stripe retries a delivery for three days
                   and does not guarantee order, so "you were paid" arrives repeatedly. Claiming
                   is a WRITE that fails on a duplicate, not a read-then-write — two deliveries
                   handled concurrently would both pass a read check and both grant the credits.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from payments.domain.payment_intent import PaymentIntent


@runtime_checkable
class PaymentIntentStore(Protocol):
    def record(self, intent: PaymentIntent, *, at: float) -> None:
        ...

    def claim_event(self, event_id: str, *, at: float) -> bool:
        """True if this delivery is the first. False means it has already been handled and the
        caller must do nothing further."""
        ...
