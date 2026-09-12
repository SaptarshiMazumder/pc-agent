"""This module's failures, named.

NOTHING HERE IS SWALLOWED AT THE POINT IT HAPPENS. A rental that half-worked is the one failure
that costs real money, so every error carries enough to act on — the marketplace's own status and
body, not a tidy "rental failed". "Could not rent" without the provider's words is unactionable
at three in the morning, which is exactly when an unexplained bill gets noticed.
"""

from __future__ import annotations


class VastError(RuntimeError):
    """Base for everything this module raises, so a caller can catch the module as a unit."""


class MarketplaceError(VastError):
    """The GPU marketplace refused or failed a call."""

    def __init__(self, what: str, status: int, body: str) -> None:
        super().__init__(f"{what}: HTTP {status} — {body[:400]}")
        self.what = what
        self.status = status
        self.body = body


class OfferGone(VastError):
    """The offer we chose was taken, or withdrawn, between the search and the rent.

    A RACE, NOT A FAULT. Candidates come back cheapest-first, and the cheapest listing is
    exactly the one every other buyer is about to take as well — so on a busy day the rent
    loses the race to the search by a second. The first time this happened the whole call
    failed ("gpu_ensure failed: … HTTP 410 — no_such_ask"), the slot was freed, and nothing
    called again. The service now answers it by moving to the next candidate from the same
    search; only when every one is gone does it become NoOfferAvailable.

    Raised by the marketplace adapter, which is the only place that knows what the provider's
    "gone" looks like; the service never sees the status code.
    """

    def __init__(self, offer_id: int, detail: str) -> None:
        super().__init__(f"offer {offer_id} was taken before it could be rented: {detail[:200]}")
        self.offer_id = offer_id


class NoOfferAvailable(VastError):
    """Nothing on the market met the VRAM floor within the price ceiling.

    A NORMAL CONDITION, NOT A BUG. The ceiling is the point: on a busy day the honest answer is
    "no machine at this price right now", and raising it here keeps that decision with the
    operator instead of letting the code quietly pay more.
    """


class SlotLost(VastError):
    """The account's slot vanished between claiming it and using it.

    Rare, and never ignorable: proceeding would rent a GPU against a slot we no longer hold,
    which is precisely how an account ends up with two.
    """


class BudgetExhausted(VastError):
    """This account has spent its allowance for the month.

    A REFUSAL, NOT A FAULT. Nothing is broken and retrying will not help until the month turns
    or someone raises the cap, so it must not read to the agent as a transient error to loop on.
    """


class CapacityFull(VastError):
    """The platform is already running as many machines as it allows at once.

    Unlike BudgetExhausted this IS transient — a machine freed a minute later admits the next
    request — so it is worth retrying, and says so.
    """
