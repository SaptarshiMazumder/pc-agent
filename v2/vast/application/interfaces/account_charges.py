"""AccountCharges — the port through which a rented machine's time becomes the person's bill.

WHY A PORT. This module rents GPUs; it does not know what a credit is, and must not: the
accounts service owns balances, the ledger and the no-overdraft rule. What vast needs is two
answers — "charge this account these dollars for this much machine time" and "can this account
pay for a machine at all" — and the host that mounts the module supplies both, so the two halves
of the business cannot disagree about what a dollar of GPU costs the user. A host that supplies
neither (a desktop daemon, a test) gets the old behaviour: the platform pays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChargeOutcome:
    """What a charge did. `covered` means the whole amount was taken. A shortfall means the
    balance ran dry inside this slice — the caller's cue that the machine must stop, because
    the next slice would be a gift."""

    covered: bool
    credits: int = 0
    shortfall: int = 0


class AccountCharges(Protocol):
    def charge(
        self, account_id: str, usd: float, *, event_id: str, label: str, agent_id: str = ""
    ) -> ChargeOutcome:
        """Debit `usd` of machine time from the account. `event_id` makes a retry harmless —
        the same id is never charged twice; `label` names what the money was for in the
        ledger; `agent_id` picks the pocket, the way a model call made from that agent does.
        NEVER RAISES for an empty balance: that is an outcome, not a fault."""
        ...

    def funded(self, account_id: str, agent_id: str = "") -> bool:
        """May this account start a machine at all — is there anything left to charge?"""
        ...
