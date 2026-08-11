"""What a paid-for order turns into, in OUR books — the accounts half of the payments seam.

`payments` decides that money is real and calls in here to find out what that MEANS. Everything
below — ledger transactions, credit grants, entitlements, subscriptions — is a concept the
payments module deliberately does not have.

FOUR THINGS HAPPEN, IN THIS ORDER, AND THE ORDER IS LOAD-BEARING:

  1. post to the ledger    the step that can legitimately REFUSE (a sale that would lose money).
                           Refusing before the credits exist is what stops a rejected purchase
                           from half-landing.
  2. add the credits       as a GRANT, not a balance, because a grant is what carries an expiry
                           and a class — and only a grant can be scoped to one agent.
  3. unlock the agent      buying a subscription also entitles you to RUN it. Money and
                           permission are separate concepts, so it is a separate row rather than
                           an implication of having credits.
  4. record the subscription

A REPLAY STOPS AFTER STEP 1. `post_purchase` reports `created=False` when this idempotency key
was already posted; the books are then already correct and must not be touched again, and above
all no second grant may be minted. That check is the difference between a retried request and a
free second pack of credits.

The ledger module is INJECTED rather than imported, because the accounts service is loaded by
path in tests and its siblings are not importable by name from there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from payments.domain.payment_intent import PaymentIntent
from payments.domain.processed_payment import ProcessedPayment


@dataclass(frozen=True)
class PurchaseOrder:
    """What was bought — read from the `products` row, never from the request.

    Carried as a value object so the processor is constructed knowing the order and then needs
    nothing but the payment: the rail hands back one argument, and it is not the shopping basket.
    """

    account_id: str
    price_usd: float
    credits: int
    scope: str = "platform"
    tier_max: str = ""
    period_days: int = 30
    creator_id: str = ""
    agent_id: str = ""
    product_id: str = ""
    idempotency_key: str = ""

    def to_metadata(self) -> dict:
        """The order, small enough to ride along with the payment.

        WHY THE WHOLE ORDER AND NOT JUST AN ID. An asynchronous payment is processed minutes or
        days later, by a request nobody started. Re-reading the products row then would deliver
        whatever the row says NOW — so an author who edits their price between a customer opening
        checkout and their bank settling would change what that customer receives. They buy what
        they were shown. The rail signs this back to us, so it is as trustworthy as the payment.
        """
        return {
            "account_id": self.account_id,
            "price_usd": f"{self.price_usd:.6f}",
            "credits": str(self.credits),
            "scope": self.scope,
            "tier_max": self.tier_max,
            "period_days": str(self.period_days),
            "creator_id": self.creator_id,
            "agent_id": self.agent_id,
            "product_id": self.product_id,
        }

    @classmethod
    def from_metadata(cls, meta: dict, *, idempotency_key: str) -> "PurchaseOrder":
        """Rebuild it on the way back. RAISES on anything missing or unparseable rather than
        defaulting: a zero-credit grant against a real payment is the worst possible repair."""
        try:
            order = cls(
                account_id=str(meta["account_id"]),
                price_usd=float(meta["price_usd"]),
                credits=int(meta["credits"]),
                scope=str(meta.get("scope") or "platform"),
                tier_max=str(meta.get("tier_max") or ""),
                period_days=int(meta.get("period_days") or 0),
                creator_id=str(meta.get("creator_id") or ""),
                agent_id=str(meta.get("agent_id") or ""),
                product_id=str(meta.get("product_id") or ""),
                idempotency_key=idempotency_key,
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"payment carries no usable order: {e}") from e
        if not order.account_id or order.credits <= 0 or order.price_usd <= 0:
            raise ValueError("payment carries an order with no account, price or credits")
        return order


class AccountsPostProcessor:
    """The customer is here, and we already know what they ordered."""

    def __init__(self, connection: Any, ledger: Any, order: PurchaseOrder, *, at: float) -> None:
        self._c = connection
        self._ledger = ledger
        self._order = order
        self._at = at

    def process(self, payment: PaymentIntent) -> ProcessedPayment:
        """Raises `ledger.LedgerError` when the posting is refused. Deliberately not caught here:
        the caller owns the HTTP status, and a purchase that cannot be booked must not look like
        one that was."""
        order, ts = self._order, self._at
        txn_id, created, split = self._ledger.post_purchase(
            self._c,
            ts,
            account_id=order.account_id,
            gross_micros=self._ledger.usd_to_micros(order.price_usd),
            credits_sold=order.credits,
            agent_id=order.agent_id,
            creator_id=order.creator_id,
            ref=payment.reference,
            idempotency_key=f"purchase:{order.idempotency_key}" if order.idempotency_key else "",
        )
        expires_at = (ts + order.period_days * 86_400) if order.period_days else 0.0
        detail = {"split": split, "expires_at": expires_at}
        if not created:
            return ProcessedPayment(reference=txn_id, created=False, detail=detail)

        self._c.execute(
            "INSERT INTO credit_grants (account_id, scope, credits, credits_used, credit_class, "
            "model_tier_max, expires_at, created_at) VALUES (?, ?, ?, 0, 'paid', ?, ?, ?)",
            (order.account_id, order.scope, order.credits, order.tier_max, expires_at, ts),
        )
        if order.agent_id:
            self._c.execute(
                "INSERT INTO entitlements (account_id, agent_id, source, expires_at, created_at) "
                "VALUES (?, ?, 'purchase', ?, ?) ON CONFLICT(account_id, agent_id) DO UPDATE SET "
                "expires_at=excluded.expires_at",
                (order.account_id, order.agent_id, expires_at, ts),
            )
            if order.product_id:
                self._c.execute(
                    "INSERT INTO subscriptions (account_id, product_id, status, renews_at, "
                    "created_at) VALUES (?, ?, 'active', ?, ?) "
                    "ON CONFLICT(account_id, product_id) DO UPDATE SET "
                    "status='active', renews_at=excluded.renews_at",
                    (order.account_id, order.product_id, expires_at, ts),
                )
        return ProcessedPayment(reference=txn_id, created=True, detail=detail)


class WebhookPostProcessor:
    """The same delivery, for a payment that finished WITHOUT us — on a callback, minutes or days
    after the customer left, on a request nobody started.

    The only difference is where the order comes from: there is no HTTP request carrying one, so
    it is rebuilt from the metadata the rail signed back to us. Co-located with the class it
    delegates to because the two are one policy seen from two directions, and splitting them
    would let the interactive and asynchronous paths drift into granting different things.

    EVERY GUARD HERE RAISES. A payment taken and not delivered must page a human: the webhook
    answering 500 is what makes the rail retry, alert on its own failing-endpoint threshold, and
    leave a trail. Swallowing it would leave a paying customer with nothing and no signal.
    """

    def __init__(self, connection: Any, ledger: Any, *, now: Any) -> None:
        self._c = connection
        self._ledger = ledger
        self._now = now

    def process(self, payment: PaymentIntent) -> ProcessedPayment:
        # The rail's own id for the checkout, NOT a client-chosen key: it is stable across
        # redeliveries, so the ledger's exactly-once guarantee holds even if the event dedupe
        # were bypassed entirely.
        order = PurchaseOrder.from_metadata(payment.meta, idempotency_key=payment.reference)
        paid = payment.amount.to_usd()
        if abs(paid - order.price_usd) > 0.01:
            raise ValueError(
                f"paid {paid:.2f} but the order says {order.price_usd:.2f}; refusing to grant "
                f"{order.credits} credits against an amount we did not quote"
            )
        if self._c.execute(
            "SELECT 1 FROM accounts WHERE id = ?", (order.account_id,)
        ).fetchone() is None:
            raise ValueError(f"payment is for unknown account {order.account_id}")
        return AccountsPostProcessor(
            self._c, self._ledger, order, at=self._now()
        ).process(payment)
