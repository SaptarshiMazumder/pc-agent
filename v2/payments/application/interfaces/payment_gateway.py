"""PaymentGateway — the rail, as the rest of the system is allowed to see it.

WHY THIS REPLACED `PaymentProvider.charge()`. The old interface had one money-taking method that
returned a terminal yes or no, synchronously, and the plan it came from claimed a real rail would
"replace ONE class and touch nothing else". That was wrong, and it was the single thing blocking
Stripe: a card payment is not synchronous. The customer is redirected, may be challenged by their
bank, and the outcome arrives minutes later on a webhook. There was nowhere in `Charge` to say so.

SO THERE ARE TWO WAYS TO TAKE MONEY, because there genuinely are two:

  ``begin_purchase``      the customer is HERE, at a keyboard. May return immediately (a rail
                          that needs no interaction) or hand back a `redirect_url` and finish
                          later over a webhook.
  ``charge_off_session``  nobody is watching — a subscription renewal against a saved card. Can
                          still come back `REQUIRES_ACTION`, which is a real outcome that has to
                          be surfaced to the customer by email, not swallowed as a decline.

`payout` IS DECLARED AND UNIMPLEMENTED, ON PURPOSE. A marketplace that can take money but not
send it is half a rail, and finding that out after choosing a provider is expensive — several
rails can charge a card and cannot pay a third party. Declaring it now means the question is
asked of every rail we consider; it is not a promise that creators can withdraw today.

EVERY METHOD IS IDEMPOTENT BY REQUIREMENT. A purchase is the one request a user retries when the
network hiccups, and every real rail keys on this. A rail bolted on later cannot retrofit it
into callers, which is why it is in the signature rather than in an implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from payments.domain.money import Money
from payments.domain.payment_intent import PaymentIntent


class PaymentConfigurationError(RuntimeError):
    """The rail cannot be built or used as configured — a missing key, an unknown provider name.

    Distinct from a declined card: this is our misconfiguration, it affects every customer, and it
    must stop the request loudly instead of degrading into a rail that takes no money.
    """


@dataclass(frozen=True)
class PurchaseRequest:
    account_id: str
    amount: Money
    #: The caller's key for this intent. One per button press: a double-click buys one pack.
    idempotency_key: str = ""
    description: str = ""
    #: Where the rail returns the customer. Ignored by rails that need no redirect.
    success_url: str = ""
    cancel_url: str = ""
    #: Travels to the rail and comes back on the webhook. This is how an asynchronous payment
    #: finds its way back to the order it was for, so it must carry everything
    #: post-processing needs.
    meta: dict = field(default_factory=dict)


@runtime_checkable
class PaymentGateway(Protocol):
    #: Displayed and stored. Nothing may BRANCH on it: if a code path only works because the rail
    #: is the mock one, that path is not built yet.
    name: str
    #: One sentence, in the rail's own words, that a client shows BEFORE the user confirms. Here
    #: rather than in the UI so that swapping the rail rewrites the disclosure too, instead of
    #: leaving a stale "no money moves" note on a page that now charges cards.
    purchase_note: str

    def begin_purchase(self, request: PurchaseRequest) -> PaymentIntent:
        ...

    def charge_off_session(self, request: PurchaseRequest) -> PaymentIntent:
        ...

    def refund(self, *, reference: str, amount: Money, idempotency_key: str = "") -> PaymentIntent:
        ...

    def payout(self, *, creator_id: str, amount: Money, idempotency_key: str = "") -> PaymentIntent:
        ...
