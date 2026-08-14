"""PaymentIntent — one attempt to move money, and where it got to.

Replaces the old `Charge`, which could only say yes or no. This can also say "I have started, send
the customer here, and I will tell you over a webhook" — the shape a card actually needs.

IT IS A RECORD OF AN ATTEMPT, NOT OF A FACT. What the rail says happened is written down
separately from what our books say happened, because when the two disagree — a charge that
succeeded at Stripe whose response we never received — that gap is the only thing reconciliation
has to work with, and it exists only if both sides were written down.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from payments.domain import payment_status
from payments.domain.money import Money

PURCHASE = "purchase"
REFUND = "refund"
PAYOUT = "payout"


@dataclass(frozen=True)
class PaymentIntent:
    kind: str  # PURCHASE | REFUND | PAYOUT
    provider: str  # the rail's own name, for display and for reconciliation
    reference: str  # the rail's id for this attempt ("" when it never got one)
    amount: Money
    status: str
    account_id: str = ""
    idempotency_key: str = ""
    #: Where to send the customer to finish paying. Empty means there is nothing for them to do.
    redirect_url: str = ""
    detail: str = ""  # the rail's own words, shown to the user; never interpreted
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        payment_status.require_known(self.status)

    @property
    def succeeded(self) -> bool:
        return self.status == payment_status.SUCCEEDED

    @property
    def failed(self) -> bool:
        return self.status == payment_status.FAILED

    @property
    def settled(self) -> bool:
        return self.status in payment_status.TERMINAL

    @property
    def awaiting_customer(self) -> bool:
        """Started, not finished, and only the customer can move it — so the caller must hand
        `redirect_url` to them rather than reporting either success or failure."""
        return bool(self.redirect_url) and not self.settled
