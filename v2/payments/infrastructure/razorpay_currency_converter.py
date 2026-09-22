"""RazorpayCurrencyConverter — the books are in USD; an Indian card is charged in INR.

WHY THIS EXISTS. Every payment link this rail created was USD, because `Money.from_usd` stamps
`currency="usd"` and the gateway passed it straight through. A USD charge on an Indian card is an
INTERNATIONAL transaction, which most Indian cards have switched off by default — so the issuer
declined it before Razorpay ever saw a problem:

    Error Source: issuer_bank · Error Step: payment_authorization · Error Reason: card_declined
    "Issuer – Card not supported, disabled or blocked"           (production, 2026-09-23)

Nothing was wrong with the keys, the link or the webhook. The customer was simply being asked to
do something their bank does not permit.

BOTH DIRECTIONS, OR THE LEDGER BREAKS. Converting only on the way out is the dangerous half-fix:
the webhook reports what was actually captured, and `Money.from_minor_units(8800, "inr")` yields
8800 rupee-ish *units* that the post-processor reads as dollars — booking about 88x the real
value into a double-entry ledger that will happily balance while being completely wrong. The
conversion belongs in one object precisely so the two halves cannot drift apart.

OPT-IN, AND OFF IS EXACTLY TODAY'S BEHAVIOUR. With no rate configured this passes USD through
untouched, so dev and any deployment not selling in India keep working unchanged.

A FIXED RATE, NOT A LIVE FEED. An FX lookup inside the payment path is a third-party service that
can be slow or down at the worst possible moment, and it makes the price a customer is shown
depend on when they clicked. One number, set deliberately, drifting against the market until
someone changes it: the drift is visible and bounded, an outage is not.
"""

from __future__ import annotations

from payments.domain.money import Money

#: What Razorpay bills Indian cards in.
INR = "inr"
USD = "usd"

#: Paise per rupee / cents per dollar. Neither currency is zero-decimal.
MINOR_UNITS_PER_UNIT = 100


class RazorpayCurrencyConverter:
    def __init__(self, inr_per_usd: float = 0.0) -> None:
        """:param inr_per_usd: rupees per dollar. 0 or absent disables conversion entirely."""
        rate = float(inr_per_usd or 0.0)
        if rate < 0:
            raise ValueError("inr_per_usd cannot be negative")
        self._rate = rate

    @property
    def enabled(self) -> bool:
        return self._rate > 0

    @property
    def charge_currency(self) -> str:
        """What the rail is told to charge in."""
        return INR.upper() if self.enabled else USD.upper()

    # ── outbound: what we ask the rail to charge ──────────────────────────────

    def to_rail(self, amount: Money) -> int:
        """Minor units in the charge currency — paise when converting, cents when not.

        RAISES rather than rounding an amount away, which is `Money.minor_units`' own rule: a
        charge the customer was never shown is worse than a refused sale.
        """
        if not self.enabled:
            return amount.minor_units()
        if amount.currency.lower() != USD:
            raise ValueError(
                f"only USD amounts convert to INR here, got {amount.currency!r} — a rail "
                f"reporting another currency must not be silently re-scaled"
            )
        paise = round(amount.to_usd() * self._rate * MINOR_UNITS_PER_UNIT)
        if paise <= 0:
            raise ValueError(
                f"{amount.to_usd()} USD at {self._rate} INR/USD rounds to zero paise — too "
                f"small to charge"
            )
        return int(paise)

    # ── inbound: what the rail says actually happened ─────────────────────────

    def from_rail(self, minor_units: int, currency: str) -> Money:
        """The rail's reported amount, as the USD the books are kept in.

        BRANCHES ON WHAT THE RAIL SAID, not on whether conversion is switched on. Payments taken
        before this existed are USD and are still refundable; converting one of those by today's
        rate would refund about a ninetieth of what the customer paid. The currency on the event
        is the only trustworthy statement of what was charged.
        """
        code = (currency or USD).strip().lower()
        if code != INR:
            return Money.from_minor_units(int(minor_units), code)
        if not self.enabled:
            # An INR payment with no rate to read it by. Guessing is how money gets invented;
            # failing here surfaces a misconfiguration while the webhook can still be retried.
            raise ValueError(
                "received an INR payment but no INR/USD rate is configured "
                "(RAZORPAY_INR_PER_USD) — refusing to guess what it was worth"
            )
        usd = int(minor_units) / MINOR_UNITS_PER_UNIT / self._rate
        return Money.from_usd(usd)
