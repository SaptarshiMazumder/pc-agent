"""PaymentsPostProcessor — what happens AFTER the money is real. IMPLEMENTED BY THE CALLER.

In plain terms: "the payment went through — now actually give them what they bought." Here that
means write the sale into the ledger, add the credits, and unlock the agent if one was bought.

THIS IS THE INVERTED DEPENDENCY, and it is the whole reason payments can be its own module. The
rail is the one that finds out a payment succeeded — sometimes on a webhook, on a request nobody
started. Something then has to move credits. If this module called into accounts to do that, it
would have to know what credits and entitlements are, and the split would be cosmetic.

Instead accounts hands in an object that knows how to finish ITS order, and payments calls it
without ever learning what "finishing" means.

IT MUST BE IDEMPOTENT. Rails redeliver. `ProcessedPayment.created=False` is the correct answer to
"you already told me this", and it must not be an error — a retried webhook is normal traffic,
not a fault.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from payments.domain.payment_intent import PaymentIntent
from payments.domain.processed_payment import ProcessedPayment


@runtime_checkable
class PaymentsPostProcessor(Protocol):
    def process(self, payment: PaymentIntent) -> ProcessedPayment:
        """Deliver what was bought. Raises if it cannot — a payment taken and not delivered is
        the one outcome that must never be swallowed."""
        ...
