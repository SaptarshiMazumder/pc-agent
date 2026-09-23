"""The admin dashboard's read-only half: how many, how much, and when.

WHY A SECOND ROUTER RATHER THAN MORE OF admin_api. That module is already 1451 lines and it is
about ADMINISTRATION — granting credits, revoking sessions, admitting creators, rotating keys.
Every route in it changes something. Everything here only counts things. Splitting them is not
tidiness: it is what lets this file state, and keep, the one rule that matters below.

NOTHING IN THIS FILE WRITES. No INSERT, no UPDATE, no DELETE, no POST route. That is the whole
safety argument for putting analytics in the same process as the money code, and it is only
worth anything while it stays literally true — so it is enforced by there being no write path
here at all, rather than by care.

THE HOT PATH IS THE CONSTRAINT, not the CPU. These routes share a process with /resolve and
/funding, the two calls in front of every model call the platform serves, and they share a
DATABASE with everything else. That is also why a separate Lambda was the wrong answer to
"don't let the dashboard affect production": it would have moved the Python and left the
queries contending exactly as hard. What actually protects the hot path is:

  * every query bounded by an explicit time window, capped at MAX_DAYS;
  * every query answered by an index (see the notes on each one);
  * every list paged, with a ceiling the client cannot raise.

A dashboard that gets slower every month eventually takes the platform down with it. The month
this stops being true is the month someone adds an unbounded query here.

DAY BUCKETS ARE COMPUTED IN SQL, PORTABLY. `created_at` and `ts` are unix seconds stored as
doubles, and the two engines disagree about date functions — so the bucket is
`FLOOR(t / 86400)`, an integer day number since the epoch, which both understand identically.
CAST alone would not do: Postgres ROUNDS a float to integer where SQLite truncates, which would
put half of every day's rows in tomorrow.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header

# A PLAIN IMPORT, not the try/_sibling dance app.py does. Under uvicorn WORKDIR /app is on
# sys.path so this resolves directly; under the by-path loader app.py registers this module in
# sys.modules BEFORE it loads this one, so the name is already there. That ordering is load
# bearing and is commented at the call site.
from sort_order_resolver import SortOrderResolver

try:  # pragma: no cover - the package is absent in unit tests
    from agentd_telemetry import count
except Exception:  # noqa: BLE001

    def count(*_a, **_k):  # type: ignore[misc]
        pass


if TYPE_CHECKING:  # pragma: no cover - types only, never imported at runtime
    from admin_api import AdminDeps


#: The widest window a caller may ask for. Not a guess: at one row per account per day this is
#: the point past which the answer stops being a dashboard and starts being a report, and a
#: report should be built from a warehouse rather than from the live identity table.
MAX_DAYS = 90
DEFAULT_DAYS = 30

#: The transactions list. Same ceiling discipline as admin_api._page and for the same reason —
#: a client asking for 100000 rows would add latency to every user's every message.
PAGE_DEFAULT = 50
PAGE_MAX = 200

SECONDS_PER_DAY = 86400.0

#: What the sign-up list may be ordered by. The aggregates are correlated subqueries rather than
#: joins so the row set stays exactly "one row per account" -- a join against credit_grants would
#: multiply an account by its number of grants and page wrongly.
#:
#: COALESCE IS LOAD-BEARING on both. An account with no grants and an account with no payments
#: produce NULL, and NULL sorts to one end regardless of direction in Postgres while SQLite puts
#: it at the other -- so without it "sort by credits" would disagree between a local database and
#: production. Zero is also the honest value: no grants IS no credits.
def signup_sorts(now: float) -> dict[str, str]:
    """The sign-up list's sortable columns, with `now` baked into the expiry test.

    A LITERAL RATHER THAN A BOUND PARAMETER, and only because of where it sits. ORDER BY is
    assembled as text, so a `?` inside it would have to be bound in a position that depends on how
    many parameters the WHERE clause happens to carry -- a rule that silently breaks the first
    time a filter is added. `now` is a float this process computed; it is not caller input, and
    formatting a float we own into SQL introduces nothing to inject.
    """
    return {
        "created_at": "a.created_at",
        "email": "lower(a.email)",
        "active": "a.active",
        "credits_remaining": (
            "COALESCE((SELECT SUM(g.credits - g.credits_used) FROM credit_grants g "
            f"WHERE g.account_id = a.id AND (g.expires_at = 0 OR g.expires_at > {float(now)!r})), 0)"
        ),
        "paid_usd": (
            "COALESCE((SELECT SUM(p.amount_usd) FROM payment_intents p "
            "WHERE p.account_id = a.id AND p.kind = 'purchase' AND p.status = 'succeeded'), 0)"
        ),
    }

#: What the payments list may be ordered by. All plain columns -- the product name lives inside a
#: JSON blob and is deliberately not sortable, because sorting by a value the database cannot see
#: would mean reading every row to order them.
TRANSACTION_SORTS = {
    "ts": "i.ts",
    "email": "lower(COALESCE(a.email, ''))",
    "amount_usd": "i.amount_usd",
    "status": "i.status",
}

#: Ledger accounts that mean "money arrived" and "money went back". Named here rather than
#: inlined so the revenue query and anyone reading it agree on what revenue IS: cash in, cash
#: out, and nothing about credits — which are a liability, not income.
CASH_ACCOUNT = "cash"


def _window(days: object) -> tuple[int, float]:
    """Clamp the requested window and return (days, cutoff_ts). Never trusts the caller."""
    try:
        n = int(days or DEFAULT_DAYS)
    except (TypeError, ValueError):
        n = DEFAULT_DAYS
    n = max(1, min(n, MAX_DAYS))
    start = datetime.now(timezone.utc) - timedelta(days=n)
    # Midnight UTC of the first day in range, so the earliest bucket is a whole day rather than
    # a partial one that reads as a slump on every chart.
    floor = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return n, floor.timestamp()


def _page(limit: object, offset: object) -> tuple[int, int]:
    try:
        size = int(limit or PAGE_DEFAULT)
    except (TypeError, ValueError):
        size = PAGE_DEFAULT
    try:
        start = int(offset or 0)
    except (TypeError, ValueError):
        start = 0
    return max(1, min(size, PAGE_MAX)), max(0, start)


def _order_of(meta: object) -> dict:
    """product_id and credits out of an intent's `meta` blob.

    THE WHOLE ORDER RIDES IN THERE (PurchaseOrder.to_metadata) because a payment settles minutes
    or days later and the products row may have changed by then -- so this reads what the
    customer was actually sold rather than what the catalogue says today. That is the number to
    quote back at somebody asking about their charge.

    A ROW WITHOUT USABLE META IS NOT AN ERROR. Refunds initiated from the rail's own dashboard
    carry none, and intents predate this column entirely; they simply have no product to name.
    """
    if not meta:
        return {}
    try:
        parsed = json.loads(meta) if isinstance(meta, (str, bytes)) else dict(meta)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out = {}
    if parsed.get("product_id"):
        out["product_id"] = str(parsed["product_id"])
    if parsed.get("credits"):
        try:
            out["credits"] = int(parsed["credits"])
        except (ValueError, TypeError):
            pass
    return out


def _day_iso(day_number: int) -> str:
    """Epoch day number back to a date the client can plot."""
    return datetime.fromtimestamp(day_number * SECONDS_PER_DAY, tz=timezone.utc).strftime(
        "%Y-%m-%d"
    )


def _dense(rows: dict[int, dict], days: int) -> list[dict]:
    """Fill the gaps.

    A GROUP BY returns nothing for a day nobody signed up, and a chart drawn straight from that
    silently closes the gap — three sign-ups on Monday and three on Friday become a flat line
    that never touched zero. Every day in the window gets a row, present or not.
    """
    today = int(datetime.now(timezone.utc).timestamp() // SECONDS_PER_DAY)
    out = []
    for d in range(today - days + 1, today + 1):
        row = rows.get(d) or {}
        out.append({"day": _day_iso(d), **{k: row.get(k, 0) for k in ("count", "gross_usd",
                                                                      "refunds_usd", "net_usd")}})
    return out


def build_admin_metrics_router(
    deps: "AdminDeps",
    require_admin: Callable[[str | None], sqlite3.Row],
) -> APIRouter:
    """Read-only aggregates over the accounts database, behind the SAME admin door.

    THE DOOR IS INJECTED, NOT IMPORTED. Reaching into admin_api for it would work — the sibling
    loader registers that module first — but only because of the order two lines happen to sit
    in, which is not a dependency anyone would notice breaking. Handing the check in makes the
    coupling visible at the call site and lets this module be constructed in a test without
    dragging the whole administration surface along with it.

    It must be the same callable admin_api uses. Two implementations of "who may administer
    this deployment" stay identical right up until one is fixed and the other is not.
    """
    router = APIRouter(prefix="/admin/metrics", tags=["admin", "metrics"])

    # ------------------------------------------------------------- sign-ups

    @router.get("/signups")
    def signups(days: int = DEFAULT_DAYS, authorization: str | None = Header(default=None)) -> dict:
        """New accounts per day.

        Rides ix_accounts_created. Before that index existed this scanned the whole identity
        table — the one table every sign-in touches — which is why the index ships with it.
        """
        require_admin(authorization)
        n, cutoff = _window(days)
        with deps.db() as c:
            rows = c.execute(
                "SELECT CAST(FLOOR(created_at / 86400.0) AS INTEGER) AS day, COUNT(*) AS n "
                "FROM accounts WHERE created_at >= ? "
                "GROUP BY CAST(FLOOR(created_at / 86400.0) AS INTEGER) ORDER BY day",
                (cutoff,),
            ).fetchall()
            total = int(c.execute("SELECT COUNT(*) n FROM accounts").fetchone()["n"])
        by_day = {int(r["day"]): {"count": int(r["n"])} for r in rows}
        series = _dense(by_day, n)
        count("admin_call_total", outcome="ok", _props={"route": "metrics/signups"})
        return {
            "days": n,
            "accounts_total": total,
            "signups_in_window": sum(p["count"] for p in series),
            "series": [{"day": p["day"], "count": p["count"]} for p in series],
        }

    @router.get("/signup-history")
    def signup_history(
        q: str = "",
        limit: int = PAGE_DEFAULT,
        offset: int = 0,
        sort: str = "created_at",
        dir: str = "desc",  # noqa: A002 - the query-string name the console sends
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Who signed up, when, and what has happened to them since. Newest first.

        THE CHART ABOVE THIS CANNOT ANSWER THE QUESTION PEOPLE ACTUALLY ASK. "Six sign-ups on
        Tuesday" is not a fact anyone can act on; "these six addresses, and one of them has paid
        us twice" is. The counts route stays because a shape over time is worth seeing at a
        glance, but it is a header, not the content.

        CREDITS ARE HERE FOR A REASON BEYOND CURIOSITY. The signup grant is written inside the
        account-creation transaction, so a new account showing zero credits is the visible
        symptom of the grant being misconfigured -- which is exactly the failure that ran
        unnoticed in production until someone thought to check a single account by hand. With
        this column that failure is obvious on the first screen of the console.

        Rides ix_accounts_created for the unfiltered case. A `q` search does not use it and falls
        back to a scan; that is acceptable because it is typed by hand, one page at a time, by
        the one person holding an admin token -- and it is bounded by the same page ceiling as
        everything else here.
        """
        require_admin(authorization)
        size, start = _page(limit, offset)
        needle = f"%{q.strip().lower()}%" if q and q.strip() else ""
        order, sort_key, sort_dir = SortOrderResolver(
            signup_sorts(deps.now()), default="created_at", tiebreak="a.id"
        ).resolve(sort, dir)
        with deps.db() as c:
            if needle:
                where = "WHERE lower(a.email) LIKE ? OR lower(a.id) LIKE ?"
                args: tuple = (needle, needle)
            else:
                where, args = "", ()
            total = int(
                c.execute(
                    f"SELECT COUNT(*) n FROM accounts a {where}", args  # noqa: S608 - fixed
                ).fetchone()["n"]
            )
            rows = c.execute(
                f"SELECT a.id, a.email, a.created_at, a.active FROM accounts a {where} "  # noqa: S608
                f"{order} LIMIT ? OFFSET ?",
                (*args, size, start),
            ).fetchall()
            ids = [str(r["id"]) for r in rows]
            credits = deps.balances.credits_for(c, ids)
            paid = deps.balances.purchases_for(c, ids)

        count("admin_call_total", outcome="ok", _props={"route": "metrics/signup-history"})
        return {
            "total": total,
            "limit": size,
            "offset": start,
            # WHAT THE SERVER DID, not what was asked. The console draws its sort arrow from
            # these, so an ignored or unknown column shows the order actually applied.
            "sort": sort_key,
            "dir": sort_dir,
            "signups": [
                {
                    "account_id": r["id"],
                    "email": r["email"],
                    "created_at": r["created_at"],
                    "active": bool(r["active"]),
                    "credits_remaining": credits.get(str(r["id"]), 0),
                    **{
                        k: paid.get(str(r["id"]), {}).get(k, 0)
                        for k in ("paid_usd", "refunded_usd", "purchases", "last_purchase_ts")
                    },
                }
                for r in rows
            ],
        }

    # -------------------------------------------------------------- revenue

    @router.get("/revenue")
    def revenue(days: int = DEFAULT_DAYS, authorization: str | None = Header(default=None)) -> dict:
        """Money in and money back, per day, from the ledger rather than from the rail.

        THE LEDGER IS THE AUTHORITY, not payment_intents. An intent records what we ASKED the
        rail for; the ledger records what the books say happened, and a refund posted by hand in
        Razorpay's dashboard reaches the ledger through the webhook without ever creating an
        intent of its own. Reading intents here would quietly under-report exactly those.

        Rides ix_entry_account(account, ts) — the predicate is `account = 'cash'` plus a ts
        range, which is that index's leading column followed by its second. No new index needed.

        DEBIT into cash is money arriving; CREDIT out of it is a refund leaving. The ledger
        stores positive micros and carries the sign in `direction`, so the split is on direction
        rather than on the sign of the amount.
        """
        require_admin(authorization)
        n, cutoff = _window(days)
        with deps.db() as c:
            rows = c.execute(
                "SELECT CAST(FLOOR(ts / 86400.0) AS INTEGER) AS day, direction, "
                "SUM(amount_micros) AS micros "
                "FROM ledger_entries WHERE account = ? AND ts >= ? "
                "GROUP BY CAST(FLOOR(ts / 86400.0) AS INTEGER), direction ORDER BY day",
                (CASH_ACCOUNT, cutoff),
            ).fetchall()

        by_day: dict[int, dict] = {}
        for r in rows:
            slot = by_day.setdefault(int(r["day"]), {"gross_usd": 0.0, "refunds_usd": 0.0})
            usd = deps.micros_to_usd(int(r["micros"] or 0))
            key = "gross_usd" if str(r["direction"]).lower() == "debit" else "refunds_usd"
            slot[key] = round(slot[key] + usd, 6)
        for slot in by_day.values():
            slot["net_usd"] = round(slot["gross_usd"] - slot["refunds_usd"], 6)

        series = _dense(by_day, n)
        out = [
            {k: p[k] for k in ("day", "gross_usd", "refunds_usd", "net_usd")} for p in series
        ]
        count("admin_call_total", outcome="ok", _props={"route": "metrics/revenue"})
        return {
            "days": n,
            "gross_usd": round(sum(p["gross_usd"] for p in out), 2),
            "refunds_usd": round(sum(p["refunds_usd"] for p in out), 2),
            "net_usd": round(sum(p["net_usd"] for p in out), 2),
            "series": out,
        }

    # --------------------------------------------------------- transactions

    @router.get("/transactions")
    def transactions(
        limit: int = PAGE_DEFAULT,
        offset: int = 0,
        status: str = "",
        sort: str = "ts",
        dir: str = "desc",  # noqa: A002 - the query-string name the console sends
        authorization: str | None = Header(default=None),
    ) -> dict:
        """The rail's own attempts, newest first, with the buyer's email beside each.

        Rides ix_intent_ts. ix_intent_acct is (account_id, ts) and cannot serve this: the
        predicate names no account, so its leading column is absent.

        A LEFT JOIN, not an inner one. An intent whose account was deleted is exactly the row an
        administrator most wants to see, and an inner join would hide it.
        """
        require_admin(authorization)
        size, start = _page(limit, offset)
        wanted = (status or "").strip().lower()
        # i.id is the autoincrement primary key — unique, so paging cannot repeat a row when a
        # hundred payments share a timestamp or an amount.
        order, sort_key, sort_dir = SortOrderResolver(
            TRANSACTION_SORTS, default="ts", tiebreak="i.id"
        ).resolve(sort, dir)
        with deps.db() as c:
            where, params = "", []
            if wanted:
                where = "WHERE i.status = ?"
                params.append(wanted)
            # COUNTED BEFORE THE PAGE, under the same predicate. Without a total the console can
            # only offer "next" and never says how many there are, which makes it impossible to
            # tell an empty last page from a broken query.
            total = int(
                c.execute(
                    f"SELECT COUNT(*) n FROM payment_intents i {where}",  # noqa: S608 - fixed
                    tuple(params),
                ).fetchone()["n"]
            )
            params.extend([size, start])
            rows = c.execute(  # noqa: S608 - `where` is a fixed string, never caller input
                "SELECT i.reference, i.account_id, i.status, i.kind, i.provider, "
                "i.amount_usd, i.currency, i.ts, i.meta, i.detail, a.email "
                f"FROM payment_intents i LEFT JOIN accounts a ON a.id = i.account_id {where} "
                f"{order} LIMIT ? OFFSET ?",
                tuple(params),
            ).fetchall()
        count("admin_call_total", outcome="ok", _props={"route": "metrics/transactions"})
        return {
            "total": total,
            "limit": size,
            "offset": start,
            "sort": sort_key,
            "dir": sort_dir,
            "transactions": [
                {
                    "reference": r["reference"],
                    "account_id": r["account_id"],
                    "email": r["email"] or "",
                    "status": r["status"],
                    # purchase / refund, and which rail took it.
                    "kind": r["kind"],
                    "provider": r["provider"],
                    # ALREADY DOLLARS. payment_intents stores amount_usd as a double, unlike the
                    # ledger's integer micros -- micros_to_usd here would divide by a million.
                    "amount_usd": float(r["amount_usd"] or 0.0),
                    "currency": r["currency"],
                    "ts": r["ts"],
                    # WHY IT FAILED, in the rail's own words. A failed payment with no reason
                    # beside it sends the operator to Razorpay's dashboard to find out, which is
                    # the exact errand this console exists to save.
                    "detail": r["detail"] or "",
                    # WHAT THEY BOUGHT, not just what they paid. An amount alone cannot tell a
                    # $5 pack from a $5 refund of a larger one, and "which pack was that?" is
                    # the first question asked about any charge someone disputes.
                    **_order_of(r["meta"]),
                }
                for r in rows
            ],
        }

    return router
