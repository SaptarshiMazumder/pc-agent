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
        with deps.db() as c:
            where, params = "", []
            if wanted:
                where = "WHERE i.status = ?"
                params.append(wanted)
            params.extend([size, start])
            rows = c.execute(  # noqa: S608 - `where` is a fixed string, never caller input
                "SELECT i.reference, i.account_id, i.status, i.kind, i.provider, "
                "i.amount_usd, i.currency, i.ts, i.meta, a.email "
                f"FROM payment_intents i LEFT JOIN accounts a ON a.id = i.account_id {where} "
                "ORDER BY i.ts DESC LIMIT ? OFFSET ?",
                tuple(params),
            ).fetchall()
        count("admin_call_total", outcome="ok", _props={"route": "metrics/transactions"})
        return {
            "limit": size,
            "offset": start,
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
                    # WHAT THEY BOUGHT, not just what they paid. An amount alone cannot tell a
                    # $5 pack from a $5 refund of a larger one, and "which pack was that?" is
                    # the first question asked about any charge someone disputes.
                    **_order_of(r["meta"]),
                }
                for r in rows
            ],
        }

    return router
