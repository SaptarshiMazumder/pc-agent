"""What a page of accounts is worth: credits left, and money actually taken.

WHY THIS IS ITS OWN MODULE AND NOT TWO PRIVATE HELPERS. Both numbers were about to be written a
second time -- once for the users tab in admin_api, once for the sign-up history in
admin_metrics_api -- and both are the kind of formula that is only right by agreement. "Credits
remaining" is `SUM(credits - credits_used)` over the grants that have not expired, where expiry
is `expires_at = 0 OR expires_at > now`; get the zero case wrong in one of the two copies and
that tab quietly reports every permanent grant as expired. Nothing would fail, and the two
screens would simply disagree about the same account forever.

A PAGE AT A TIME, NEVER A ROW AT A TIME. Each method answers for a whole list of account ids in
one statement. The per-row shape is the obvious way to write this and turns a 50-row listing
into 50 round trips against the database that also serves /resolve and /funding -- the two calls
sitting in front of every model call the platform makes. The listing being slow would be a
nuisance; the hot path contending with it is an outage.

IT READS. It never writes, and it must not learn to: it is used from the analytics router, whose
entire safety argument is that no write path exists there at all.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

#: Intent rows that represent money we actually took. A `pending` or `requires_action` intent is
#: a customer who started a checkout -- counting it as revenue is how a dashboard invents money
#: that never arrived. `refunded` is excluded here because the refund is reported beside it
#: rather than folded into the gross.
SETTLED = "succeeded"

#: Money leaving again. Kept distinct from a failed purchase, which never took anything.
REFUNDED = "refunded"

PURCHASE = "purchase"


class AccountBalanceReader:
    """Aggregates over a page of accounts, with `now` injected so tests can fix the clock.

    The connection is passed per call rather than held, because callers already own one for the
    duration of a request and opening a second inside a handler is how a connection pool runs
    dry under load.
    """

    def __init__(self, now: Callable[[], float]) -> None:
        self._now = now

    # ------------------------------------------------------------------ credits

    def credits_for(self, c: sqlite3.Connection, ids: list[str]) -> dict[str, int]:
        """Live credit balance per account id. Absent from the result means zero.

        `expires_at = 0` is the sentinel for "never expires", NOT a timestamp in 1970 -- which is
        why the predicate cannot be the obvious `expires_at > ?` alone. A grant that has run out
        of credits still matches; it simply contributes nothing, and leaving it in keeps the
        query on the index rather than adding a condition that cannot use one.
        """
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        return {
            str(r["account_id"]): int(r["total"] or 0)
            for r in c.execute(
                f"SELECT account_id, SUM(credits - credits_used) total "  # noqa: S608 - '?' only
                f"FROM credit_grants WHERE account_id IN ({marks}) "
                f"AND (expires_at = 0 OR expires_at > ?) GROUP BY account_id",
                (*ids, self._now()),
            )
        }

    # ----------------------------------------------------------------- payments

    def purchases_for(
        self, c: sqlite3.Connection, ids: list[str]
    ) -> dict[str, dict[str, float]]:
        """Per account: what they have paid, what came back, and when they last bought.

        THIS IS THE ANSWER TO "is this person a customer or a tourist", which is the one thing a
        list of sign-ups cannot tell you on its own. A thousand accounts that each took the free
        grant and left look exactly like a thousand accounts that paid, until this column exists.

        Rides ix_intent_acct(account_id, ts): the predicate leads with account_id, which is that
        index's first column.

        GROSS AND REFUNDS ARE REPORTED SEPARATELY, not netted. Someone who paid $100 and was
        refunded $100 is a materially different customer from someone who never paid at all, and
        a single net figure of zero cannot tell them apart.
        """
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        out: dict[str, dict[str, float]] = {}
        for r in c.execute(
            f"SELECT account_id, status, SUM(amount_usd) total, "  # noqa: S608 - '?' only
            f"COUNT(*) n, MAX(ts) last_ts FROM payment_intents "
            f"WHERE account_id IN ({marks}) AND kind = ? AND status IN (?, ?) "
            f"GROUP BY account_id, status",
            (*ids, PURCHASE, SETTLED, REFUNDED),
        ):
            slot = out.setdefault(
                str(r["account_id"]),
                {"paid_usd": 0.0, "refunded_usd": 0.0, "purchases": 0, "last_purchase_ts": 0.0},
            )
            amount = float(r["total"] or 0.0)
            if str(r["status"]) == SETTLED:
                slot["paid_usd"] = round(amount, 6)
                slot["purchases"] = int(r["n"] or 0)
                slot["last_purchase_ts"] = float(r["last_ts"] or 0.0)
            else:
                slot["refunded_usd"] = round(amount, 6)
        return out


__all__ = ["AccountBalanceReader", "PURCHASE", "REFUNDED", "SETTLED"]
