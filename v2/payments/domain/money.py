"""Money — an amount in INTEGER micros, plus its currency.

WHY NOT A FLOAT. `0.1 + 0.2 != 0.3` is a curiosity in a chart and a lost cent in a ledger. The
accounts ledger already reasons in micros for exactly this reason (`ledger.MICROS`); this is the
same unit, so nothing is converted between the two systems.

WHY THE CURRENCY TRAVELS WITH IT. Every rail takes an integer in the currency's MINOR unit, and
what that unit is depends on the currency: 100 JPY is `100`, but 100 USD is `10000`. A bare
number cannot be converted safely, and the failure mode is a 100x overcharge — so the amount and
its currency are one value that cannot be separated by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

MICROS_PER_UNIT = 1_000_000

#: Currencies whose minor unit IS the whole unit. Passing one of these multiplied by 100 charges
#: a hundred times the intended amount, which is why this list is here rather than assumed away.
ZERO_DECIMAL_CURRENCIES = frozenset(
    {
        "bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga",
        "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf",
    }
)


@dataclass(frozen=True)
class Money:
    micros: int
    currency: str = "usd"

    def __post_init__(self) -> None:
        if not isinstance(self.micros, int):
            raise TypeError(f"micros must be an int, got {type(self.micros).__name__}")
        if not self.currency:
            raise ValueError("currency is required")

    @classmethod
    def from_usd(cls, usd: float) -> "Money":
        """From the float the products table stores. Rounds to the nearest micro — the same
        rounding `ledger.usd_to_micros` applies, so a price cannot mean two amounts."""
        return cls(int(round(float(usd) * MICROS_PER_UNIT)), "usd")

    @classmethod
    def from_minor_units(cls, units: int, currency: str = "usd") -> "Money":
        """From what a rail reports back, so a webhook's number reaches the books unrounded."""
        code = currency.strip().lower()
        scale = MICROS_PER_UNIT if code in ZERO_DECIMAL_CURRENCIES else MICROS_PER_UNIT // 100
        return cls(int(units) * scale, code)

    def to_usd(self) -> float:
        return self.micros / MICROS_PER_UNIT

    def minor_units(self) -> int:
        """What the rail is actually sent.

        RAISES on an amount too fine to charge (half a cent) rather than rounding it away. A
        silent round here is a price the customer was never shown, and it compounds: it is
        applied to the charge but not to the credits granted, so the books drift by design.
        """
        code = self.currency.strip().lower()
        scale = MICROS_PER_UNIT if code in ZERO_DECIMAL_CURRENCIES else MICROS_PER_UNIT // 100
        if self.micros % scale:
            raise ValueError(
                f"{self.to_usd()} {code} is not a whole number of minor units "
                f"({self.micros} micros); no rail can charge it"
            )
        return self.micros // scale

    @property
    def positive(self) -> bool:
        return self.micros > 0

    def __str__(self) -> str:
        return f"{self.to_usd():.2f} {self.currency.upper()}"
