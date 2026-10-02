"""A generation would cost more than the campaign has left — refused BEFORE it is paid for.

Raised by a generator adapter that can price a job before submitting it (from its model spec, or
the provider's own quote); a step treats it exactly as reaching the budget.
"""

from __future__ import annotations


class BudgetExceeded(Exception):
    def __init__(self, model: str, cost_usd: float, left_usd: float) -> None:
        super().__init__(f"{model} would cost ${cost_usd:.2f} and the budget has ${left_usd:.2f} left")
        self.cost_usd = cost_usd
        self.left_usd = left_usd
