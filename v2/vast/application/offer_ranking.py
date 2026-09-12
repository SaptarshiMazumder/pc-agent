"""Which of the rentable machines to rent: the best card the ceiling allows, cheapest of it.

WHY NOT CHEAPEST-FIRST. Under a price ceiling, "cheapest that clears the filters" reliably
picks the weakest thing that clears them — the L4 at $0.34 while an RTX PRO 6000 sat at $1.74 —
because price is the only axis it has. PC_Rent's Vast fleet, which has rented datacenter cards
well for months, does the opposite: a curated list of cards in quality order, and price only
decides between two offers of the SAME card. The ceiling still bounds the bill; it just stops
being the thing that chooses.

THE CATALOGUE IS MATCHED BY LONGEST SUBSTRING, not equality. Vast lists the same card under
variants — "RTX PRO 6000 S", "RTX PRO 6000 Max-Q", "RTX PRO 6000 WS" — and an exact match on
"RTX PRO 6000" finds none of them (PC_Rent's own fan-out saw 7 of 23 candidates for exactly this
reason). Longest match so that "L40S" is never mistaken for "L40".

A card not in the catalogue is not rented. That is the allowlist back, on purpose and with a
better reason than before: the list is not "cards that exist", it is "cards worth paying for",
and it comes with an order.
"""

from __future__ import annotations

from vast.domain.instance import Offer


def tier_of(gpu_name: str, catalogue: tuple) -> tuple[str, float] | None:
    """(catalogue name, quality) for this offer's card, or None if it is not a card we rent."""
    name = (gpu_name or "").lower()
    best: tuple[str, float] | None = None
    for cat, quality in catalogue:
        if cat.lower() in name and (best is None or len(cat) > len(best[0])):
            best = (cat, float(quality))
    return best


def rank_offers(offers: list[Offer], catalogue: tuple) -> list[Offer]:
    """The offers worth renting, best first: quality tier descending, price ascending within it.

    Everything the adapter already filtered (ceiling, tier, reliability, CUDA, disk) is assumed
    filtered; this only orders and drops cards that are not in the catalogue. An empty
    catalogue means "any card", ordered by price alone — the old behaviour, kept reachable.
    """
    if not catalogue:
        return sorted(offers, key=lambda o: o.hourly_usd)
    keep: list[tuple[float, float, Offer]] = []
    for o in offers:
        tier = tier_of(o.gpu_name, catalogue)
        if tier is None:
            continue
        keep.append((-tier[1], o.hourly_usd, o))
    keep.sort(key=lambda t: (t[0], t[1]))
    return [o for _, _, o in keep]
