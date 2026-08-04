"""The payment rail, behind one interface (plan item 2.3, decision D8).

WHY A SEAM AND NOT A TODO. Payments are blocked on things code cannot fix — a company, a
merchant account, a country Stripe serves. What is NOT blocked is everything that happens around
a payment: the liability, the reserve, the creator's accrual, the receipt. Those are built for
real now (see ledger.py), against an interface whose only implementation currently moves no
money. Adding a real rail later replaces ONE class and touches nothing else.

WHAT "MOCKED" MEANS PRECISELY. `NullPaymentProvider.charge()` always succeeds and returns a
provider reference that is obviously fake (`null_...`). It records intent — an amount, a
currency, a payer — and moves nothing. Every downstream consequence is real. Nothing in the
system may branch on "is this the null provider": if a code path only works because payments are
fake, it is not built yet.

IDEMPOTENCY IS PART OF THE INTERFACE, not an implementation detail, because every real rail
requires it and a rail bolted on later cannot retrofit it into callers.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Charge:
    """The outcome of attempting to take money. `ok=False` is a declined card, not an outage —
    an outage raises."""

    ok: bool
    provider: str
    reference: str
    amount_usd: float
    currency: str = "usd"
    status: str = ""
    detail: str = ""
    meta: dict = field(default_factory=dict)


class PaymentProvider:
    """charge / refund / payout — the three money movements the platform ever needs.

    `payout` is here from the start even though nothing calls it yet: a marketplace that can
    take money but not send it is only half a rail, and discovering that after choosing a
    provider is expensive. Several rails can charge but cannot pay third parties.
    """

    name = "abstract"

    def charge(self, *, account_id: str, amount_usd: float, idempotency_key: str,
               description: str = "", meta: dict | None = None) -> Charge:
        raise NotImplementedError

    def refund(self, *, reference: str, amount_usd: float, idempotency_key: str) -> Charge:
        raise NotImplementedError

    def payout(self, *, creator_id: str, amount_usd: float, idempotency_key: str) -> Charge:
        raise NotImplementedError


class NullPaymentProvider(PaymentProvider):
    """Records intent, moves nothing. The only implementation today."""

    name = "null"

    def _ref(self, kind: str, idempotency_key: str) -> str:
        # Derived from the idempotency key so replaying a request yields the SAME reference,
        # exactly as a real rail would. A random reference per call would let the mock pass
        # tests that the real thing would fail.
        seed = idempotency_key or uuid.uuid4().hex
        return f"null_{kind}_{abs(hash(seed)) & 0xFFFFFFFF:08x}"

    def charge(self, *, account_id: str, amount_usd: float, idempotency_key: str,
               description: str = "", meta: dict | None = None) -> Charge:
        if amount_usd <= 0:
            return Charge(False, self.name, "", amount_usd, status="invalid_amount",
                          detail="amount must be positive")
        return Charge(
            True, self.name, self._ref("ch", idempotency_key), float(amount_usd),
            status="succeeded", detail="mocked: no money moved",
            meta={"account_id": account_id, "description": description, **(meta or {})},
        )

    def refund(self, *, reference: str, amount_usd: float, idempotency_key: str) -> Charge:
        return Charge(True, self.name, self._ref("re", idempotency_key), float(amount_usd),
                      status="refunded", detail="mocked: no money moved",
                      meta={"original": reference})

    def payout(self, *, creator_id: str, amount_usd: float, idempotency_key: str) -> Charge:
        return Charge(True, self.name, self._ref("po", idempotency_key), float(amount_usd),
                      status="paid", detail="mocked: no money moved",
                      meta={"creator_id": creator_id})


_REGISTRY: dict[str, type[PaymentProvider]] = {"null": NullPaymentProvider}


def provider() -> PaymentProvider:
    """The configured rail. Unknown names fall back to null rather than failing to boot —
    identity and sign-in must not go down because a payment setting is wrong."""
    name = (os.environ.get("AGENTD_PAYMENT_PROVIDER", "null") or "null").strip().lower()
    return _REGISTRY.get(name, NullPaymentProvider)()


def schema(c) -> None:
    """Record of every attempt to move money, whether or not it succeeded.

    Separate from the ledger on purpose: the ledger records what is TRUE about our books, this
    records what we ASKED A THIRD PARTY TO DO. When they disagree — a charge that succeeded at
    the rail but whose response was lost — that gap is the thing reconciliation looks for, and
    it only exists if both sides were written down.
    """
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS payment_intents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              REAL NOT NULL,
            kind            TEXT NOT NULL,            -- charge | refund | payout
            provider        TEXT NOT NULL,
            reference       TEXT NOT NULL DEFAULT '',
            account_id      TEXT NOT NULL DEFAULT '',
            amount_usd      REAL NOT NULL DEFAULT 0,
            currency        TEXT NOT NULL DEFAULT 'usd',
            status          TEXT NOT NULL DEFAULT '',
            detail          TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL DEFAULT ''
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_intent_idem
            ON payment_intents(idempotency_key) WHERE idempotency_key <> '';
        CREATE INDEX IF NOT EXISTS ix_intent_acct ON payment_intents(account_id, ts);
        """
    )


def record(c, ts: float, kind: str, result: Charge, *, account_id: str,
           idempotency_key: str) -> None:
    """Persist an attempt. Ignores a duplicate key so a replayed request does not error."""
    c.execute(
        "INSERT INTO payment_intents (ts, kind, provider, reference, account_id, amount_usd, "
        "currency, status, detail, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(idempotency_key) WHERE idempotency_key <> '' DO NOTHING",
        (ts, kind, result.provider, result.reference, account_id, result.amount_usd,
         result.currency, result.status, result.detail, idempotency_key),
    )
