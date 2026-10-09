"""A generation would cost more than the person's credits cover — refused BEFORE it is paid for.

Raised by a generator adapter that can price a job before submitting it (from its model spec, or
the provider's own quote), with `max_usd` set to what the balance covers.
"""

from __future__ import annotations


class BudgetExceeded(Exception):
    def __init__(self, model: str, cost_usd: float, left_usd: float) -> None:
        super().__init__(f"{model} would cost ${cost_usd:.2f} and the credits left cover ${left_usd:.2f}")
        self.cost_usd = cost_usd
        self.left_usd = left_usd
