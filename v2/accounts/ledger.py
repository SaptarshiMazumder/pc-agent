"""Double-entry ledger for the platform's money (plan items 2.1 / 2.4).

WHY DOUBLE ENTRY, WHEN PAYMENTS ARE STILL MOCKED. Money history cannot be backfilled. The
payment rail is fake today, but every consequence of a purchase — the liability we take on, the
inference we owe a provider, the share a creator has earned — is real the moment it happens, and
reconstructing it later from logs is impossible. So the accounting is real from day one and only
the rail is fake (D8).

WHAT DOUBLE ENTRY BUYS. A single `balance` column can be wrong and nothing notices. Here every
event writes entries that MUST sum to zero, in one transaction, and any balance is re-derivable
by replaying entries. If the books do not balance the write is rejected rather than accepted and
silently wrong — which is the whole point when the alternative is discovering it at year end.

THE FIVE EVENTS
  purchase       user pays -> we owe them service, we owe a provider, a creator has earned
  promo_grant    we give credits away -> same liability, no cash, and NEVER a creator accrual
  consumption    a model call ran -> provider billed, some of the liability becomes revenue
  expiry         unspent credits died -> liability becomes breakage revenue
  payout         we actually pay a creator -> the accrued liability is settled

UNITS. Integer MICRO-DOLLARS (1 USD = 1_000_000). Floats accumulate rounding error, and money
that does not add up is indistinguishable from money that was stolen.

SIGN CONVENTION. Every amount stored is POSITIVE; `direction` carries the sign. Assets and
expenses increase on the debit side; liabilities and revenue increase on the credit side. That
is what `balance_of` normalises, so a caller never has to remember which way round an account is.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid

MICROS = 1_000_000

ASSET = "asset"
LIABILITY = "liability"
REVENUE = "revenue"
EXPENSE = "expense"

#: The chart of accounts. Adding a bucket here is the only thing needed to start posting to it.
CHART: dict[str, str] = {
    # assets
    "cash": ASSET,
    # Cash deliberately fenced off to pay providers for inference already sold. NOT ours to
    # distribute: it is the reason a creator can be paid on day 1 for a service delivered over
    # 30 days (D3).
    "inference_reserve": ASSET,
    # liabilities
    # Prepaid credits are a LIABILITY, not revenue on arrival. We have been paid for a service
    # we have not yet delivered; it becomes revenue only as it is consumed or expires.
    "user_credit_liability": LIABILITY,
    "creator_payable": LIABILITY,
    # revenue
    "platform_revenue": REVENUE,
    "breakage_revenue": REVENUE,
    # expenses
    "provider_cost": EXPENSE,
    "processing_fees": EXPENSE,
    "creator_cost": EXPENSE,
    "promotional_cost": EXPENSE,
}

DEBIT = "debit"
CREDIT = "credit"


class LedgerError(ValueError):
    """A posting that would leave the books inconsistent. Always a bug, never user input."""


# --- configuration -----------------------------------------------------------
# Read per call, not at import, so a deployment can retune without a rebuild and so tests can
# vary them. Every one of these is a business decision with an open question behind it; the
# defaults are seeds, not answers.


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def credits_per_usd() -> float:
    """Credits per USD of PROVIDER COST. Must match model_proxy/metering.py or the two halves
    of the business disagree about what a credit is worth."""
    return _env_float("AGENTD_CREDITS_PER_USD", 166_667.0)


def credit_markup() -> float:
    """How much more a credit SELLS for than it COSTS. THE MARGIN DIAL.

    Easy to miss that it must exist at all: `credits_for(cost)` converts provider cost to credits
    at `credits_per_usd`, and selling credits at that SAME rate means $20 buys exactly $20 of
    provider cost — gross margin ZERO, before fees. Two different questions were sharing one
    number.

    COUPLED TO credits_per_usd, DELIBERATELY. That constant was sized backwards from a target
    price (metering.py: "$20 should buy 1,000,000 credits at a 30% cost ratio"), which only holds
    at a markup of 20/6 = 3.3333. The default therefore matches that sizing rather than being an
    independent guess: $20 -> ~1,000,000 credits -> $6 of inference -> 30% cost ratio. Changing
    one of the two without the other silently moves the product's price.

    markup = 1 / cost_ratio.  3.3333 -> 30% of revenue is inference. 2.0 -> 50%. 1.0 -> a loss.
    """
    return max(1.0, _env_float("AGENTD_CREDIT_MARKUP", 3.3333))


def processing_fee_pct() -> float:
    """What the payment rail takes. Zero while payments are mocked would flatter every margin
    number we look at, so the default is a realistic card rate."""
    return _env_float("AGENTD_PROCESSING_FEE_PCT", 0.03)


def creator_share_pct() -> float:
    """The creator's cut OF MARGIN, not of gross (D4). Open question O1."""
    return _env_float("AGENTD_CREATOR_SHARE_PCT", 0.80)


def allow_negative_margin() -> bool:
    """Refuse a sale that loses money, unless explicitly permitted.

    With the default markup a purchase is always profitable, but a misconfigured markup or a
    fee above the margin would otherwise sell at a loss silently — and at volume. Fail loudly.
    """
    return os.environ.get("AGENTD_ALLOW_NEGATIVE_MARGIN", "").strip().lower() in ("1", "true", "yes")


def usd_to_micros(usd: float) -> int:
    return int(round(float(usd) * MICROS))


def micros_to_usd(micros: int) -> float:
    return round(int(micros) / MICROS, 6)


def credits_to_cost_micros(credits: int) -> int:
    """What `credits` will cost US in provider spend if every one is used.

    Exact, not estimated — and that is only true because the cap is hard (D2). No overdraft
    means worst-case inference for a grant is a number we can compute at the moment of sale,
    which is what makes reserving it (and paying a creator immediately) safe.
    """
    rate = credits_per_usd()
    if rate <= 0:
        return 0
    return int(round(int(credits) / rate * MICROS))


def credits_for_usd(usd: float) -> int:
    """How many credits a purchase of `usd` buys, after markup. Floor: never sell more service
    than was paid for."""
    rate = credits_per_usd()
    markup = credit_markup()
    if rate <= 0 or markup <= 0:
        return 0
    return int(float(usd) * rate / markup)


# --- schema ------------------------------------------------------------------


def schema(c: sqlite3.Connection) -> None:
    """Create the ledger tables. Idempotent; safe on every boot."""
    c.executescript(
        """
        -- One row per money EVENT. Exists separately from the entries so an idempotency key has
        -- somewhere unique to live, and so a replayed request can return the original txn_id
        -- instead of posting the same money twice.
        CREATE TABLE IF NOT EXISTS ledger_txns (
            txn_id          TEXT PRIMARY KEY,
            txn_type        TEXT NOT NULL,
            ts              REAL NOT NULL,
            account_id      TEXT NOT NULL DEFAULT '',
            agent_id        TEXT NOT NULL DEFAULT '',
            ref             TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL DEFAULT '',
            meta            TEXT NOT NULL DEFAULT ''
        );
        -- PARTIAL index: rows without a key must not collide with each other, and most
        -- internally generated postings have no natural key.
        CREATE UNIQUE INDEX IF NOT EXISTS ix_txn_idem
            ON ledger_txns(idempotency_key) WHERE idempotency_key <> '';
        CREATE INDEX IF NOT EXISTS ix_txn_acct ON ledger_txns(account_id, ts);

        -- The entries themselves. Append-only: never UPDATE, never DELETE. A mistake is
        -- corrected by posting its reversal, so the history of what we believed stays intact.
        CREATE TABLE IF NOT EXISTS ledger_entries (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id        TEXT NOT NULL,
            txn_type      TEXT NOT NULL,
            ts            REAL NOT NULL,
            account       TEXT NOT NULL,           -- a key of CHART
            direction     TEXT NOT NULL,           -- debit | credit
            amount_micros INTEGER NOT NULL,        -- always POSITIVE
            account_id    TEXT NOT NULL DEFAULT '',
            agent_id      TEXT NOT NULL DEFAULT '',
            ref           TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_entry_txn     ON ledger_entries(txn_id);
        CREATE INDEX IF NOT EXISTS ix_entry_account ON ledger_entries(account, ts);
        CREATE INDEX IF NOT EXISTS ix_entry_acct    ON ledger_entries(account_id, ts);
        """
    )


# --- posting -----------------------------------------------------------------


def post(
    c: sqlite3.Connection,
    txn_type: str,
    entries: list[tuple[str, str, int]],
    ts: float,
    *,
    account_id: str = "",
    agent_id: str = "",
    ref: str = "",
    idempotency_key: str = "",
    meta: dict | None = None,
) -> tuple[str, bool]:
    """Write one balanced transaction. Returns (txn_id, created).

    `entries` is [(account, direction, amount_micros), ...]. Rejects anything that would leave
    the books inconsistent: an unknown account, a non-positive amount, or debits that do not
    equal credits. Rejecting is the feature — a ledger that accepts a bad posting is worse than
    one that refuses, because the error becomes invisible.

    `created=False` means an identical `idempotency_key` was already posted and nothing was
    written. Callers rely on this: the proxy's retry buffer replays writes whose response was
    lost, and "the response was lost" is indistinguishable from "the write failed".
    """
    if not entries:
        raise LedgerError("a transaction must have entries")

    debits = credits_ = 0
    for account, direction, amount in entries:
        if account not in CHART:
            raise LedgerError(f"unknown account {account!r}")
        if direction not in (DEBIT, CREDIT):
            raise LedgerError(f"bad direction {direction!r}")
        if int(amount) <= 0:
            raise LedgerError(f"amount must be positive, got {amount} on {account!r}")
        if direction == DEBIT:
            debits += int(amount)
        else:
            credits_ += int(amount)
    if debits != credits_:
        raise LedgerError(f"unbalanced: debits {debits} != credits {credits_}")

    if idempotency_key:
        seen = c.execute(
            "SELECT txn_id FROM ledger_txns WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if seen is not None:
            return str(seen["txn_id"] if isinstance(seen, sqlite3.Row) else seen[0]), False

    txn_id = uuid.uuid4().hex
    c.execute(
        "INSERT INTO ledger_txns (txn_id, txn_type, ts, account_id, agent_id, ref, "
        "idempotency_key, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (txn_id, txn_type, ts, account_id, agent_id, ref, idempotency_key,
         json.dumps(meta or {}, separators=(",", ":"))),
    )
    c.executemany(
        "INSERT INTO ledger_entries (txn_id, txn_type, ts, account, direction, amount_micros, "
        "account_id, agent_id, ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(txn_id, txn_type, ts, a, d, int(m), account_id, agent_id, ref) for a, d, m in entries],
    )
    return txn_id, True


# --- the five events ---------------------------------------------------------


def split_purchase(gross_micros: int, credits_sold: int) -> dict:
    """Divide one payment three ways: fees, reserved inference, distributable margin (D3).

    Pure arithmetic, separated from the posting so the money split can be unit-tested and shown
    to a user without touching a database.

    THE RESERVE IS EXACT, NOT ESTIMATED. It is what those credits will cost us if every single
    one is spent — knowable only because the cap is hard. Everything left after fees and reserve
    is real margin that can be distributed the same day without gambling on future usage.
    """
    fee = int(round(gross_micros * processing_fee_pct()))
    reserve = credits_to_cost_micros(credits_sold)
    margin = gross_micros - fee - reserve
    creator = int(round(margin * creator_share_pct())) if margin > 0 else 0
    platform = margin - creator
    return {
        "gross_micros": int(gross_micros),
        "fee_micros": fee,
        "reserve_micros": reserve,
        "margin_micros": margin,
        "creator_micros": creator,
        "platform_micros": platform,
    }


def post_purchase(
    c: sqlite3.Connection,
    ts: float,
    *,
    account_id: str,
    gross_micros: int,
    credits_sold: int,
    agent_id: str = "",
    creator_id: str = "",
    ref: str = "",
    idempotency_key: str = "",
) -> tuple[str, bool, dict]:
    """A user paid. Four balanced postings; see split_purchase for the arithmetic."""
    split = split_purchase(gross_micros, credits_sold)
    if split["margin_micros"] < 0 and not allow_negative_margin():
        raise LedgerError(
            f"purchase would lose {micros_to_usd(-split['margin_micros'])} USD "
            f"(gross {micros_to_usd(gross_micros)}, fee {micros_to_usd(split['fee_micros'])}, "
            f"reserve {micros_to_usd(split['reserve_micros'])}). Raise AGENTD_CREDIT_MARKUP, "
            f"or set AGENTD_ALLOW_NEGATIVE_MARGIN=1 if this is deliberate."
        )

    entries: list[tuple[str, str, int]] = [
        # Cash in, and an equal obligation to deliver service later.
        ("cash", DEBIT, gross_micros),
        ("user_credit_liability", CREDIT, gross_micros),
    ]
    if split["fee_micros"] > 0:
        entries += [("processing_fees", DEBIT, split["fee_micros"]),
                    ("cash", CREDIT, split["fee_micros"])]
    if split["reserve_micros"] > 0:
        entries += [("inference_reserve", DEBIT, split["reserve_micros"]),
                    ("cash", CREDIT, split["reserve_micros"])]
    if split["creator_micros"] > 0 and creator_id:
        # Accrued, not paid. `payout` settles it later; until then it is money we owe.
        entries += [("creator_cost", DEBIT, split["creator_micros"]),
                    ("creator_payable", CREDIT, split["creator_micros"])]
    else:
        split["creator_micros"] = 0
        split["platform_micros"] = split["margin_micros"]

    txn_id, created = post(
        c, "purchase", entries, ts,
        account_id=account_id, agent_id=agent_id, ref=ref,
        idempotency_key=idempotency_key,
        meta={"credits_sold": credits_sold, "creator_id": creator_id, **split},
    )
    return txn_id, created, split


def post_promotional_grant(
    c: sqlite3.Connection,
    ts: float,
    *,
    account_id: str,
    credits: int,
    agent_id: str = "",
    ref: str = "",
    idempotency_key: str = "",
) -> tuple[str, bool]:
    """Credits given away. Still a liability — we owe the service — but funded by us.

    DELIBERATELY NO CREATOR ACCRUAL. Free credits plus a creator payout is a money printer:
    anyone could mint promotional credits, spend them on their own agent, and be paid real
    money for it. This is the code-level half of that rule; `credit_class` is the data half.
    """
    value = credits_to_cost_micros(credits)
    if value <= 0:
        return "", False
    return post(
        c, "promo_grant",
        [("promotional_cost", DEBIT, value), ("user_credit_liability", CREDIT, value)],
        ts, account_id=account_id, agent_id=agent_id, ref=ref,
        idempotency_key=idempotency_key, meta={"credits": credits},
    )


def post_consumption(
    c: sqlite3.Connection,
    ts: float,
    *,
    account_id: str,
    cost_micros: int,
    credits_charged: int,
    agent_id: str = "",
    ref: str = "",
    idempotency_key: str = "",
) -> tuple[str, bool]:
    """A model call completed: a provider must be paid, and prepaid service was delivered.

    Two independent halves, and they are NOT the same number:
      * we owe a provider `cost_micros`, paid out of the reserve set aside at purchase
      * the user consumed `credits_charged` of what they prepaid, so that much liability
        becomes recognised revenue

    Their difference over time IS the margin, visible without any separate calculation.
    """
    entries: list[tuple[str, str, int]] = []
    if cost_micros > 0:
        entries += [("provider_cost", DEBIT, cost_micros),
                    ("inference_reserve", CREDIT, cost_micros)]
    recognised = credits_to_cost_micros(credits_charged)
    if recognised > 0:
        entries += [("user_credit_liability", DEBIT, recognised),
                    ("platform_revenue", CREDIT, recognised)]
    if not entries:
        return "", False
    return post(
        c, "consumption", entries, ts,
        account_id=account_id, agent_id=agent_id, ref=ref,
        idempotency_key=idempotency_key,
        meta={"cost_micros": cost_micros, "credits_charged": credits_charged},
    )


def post_expiry(
    c: sqlite3.Connection,
    ts: float,
    *,
    account_id: str,
    credits_unused: int,
    grant_id: int,
    idempotency_key: str = "",
) -> tuple[str, bool]:
    """Credits expired unspent. BREAKAGE — the liability is discharged without delivering
    service, so it becomes revenue, and the inference reserved against it is released.

    This is expected to be a large margin line and it cannot be guessed, only measured. It is
    also why credits must expire at all (D5): an allowance that never dies is an open-ended
    liability standing against money already paid out to a creator.
    """
    value = credits_to_cost_micros(credits_unused)
    if value <= 0:
        return "", False
    return post(
        c, "expiry",
        [
            ("user_credit_liability", DEBIT, value),
            ("breakage_revenue", CREDIT, value),
            # The reserve held for inference that will now never happen goes back to cash.
            ("cash", DEBIT, value),
            ("inference_reserve", CREDIT, value),
        ],
        ts, account_id=account_id, ref=f"grant:{grant_id}",
        idempotency_key=idempotency_key or f"expiry:{grant_id}",
        meta={"credits_unused": credits_unused, "grant_id": grant_id},
    )


def post_payout(
    c: sqlite3.Connection,
    ts: float,
    *,
    creator_id: str,
    amount_micros: int,
    ref: str = "",
    idempotency_key: str = "",
) -> tuple[str, bool]:
    """Money actually leaves for a creator, settling what was accrued at purchase."""
    if amount_micros <= 0:
        return "", False
    return post(
        c, "payout",
        [("creator_payable", DEBIT, amount_micros), ("cash", CREDIT, amount_micros)],
        ts, account_id=creator_id, ref=ref, idempotency_key=idempotency_key,
    )


# --- reading -----------------------------------------------------------------


def balance_of(c: sqlite3.Connection, account: str) -> int:
    """Signed balance in micros, normalised so a healthy account reads POSITIVE.

    Assets and expenses grow on the debit side; liabilities and revenue on the credit side.
    Normalising here means no caller has to remember which is which.
    """
    row = c.execute(
        "SELECT COALESCE(SUM(CASE direction WHEN 'debit' THEN amount_micros "
        "ELSE -amount_micros END), 0) AS net FROM ledger_entries WHERE account = ?",
        (account,),
    ).fetchone()
    net = int(row["net"] if isinstance(row, sqlite3.Row) else row[0])
    return net if CHART.get(account) in (ASSET, EXPENSE) else -net


def balances(c: sqlite3.Connection) -> dict:
    """Every account, plus the two checks worth running: do the books balance, and is the
    reserve still able to cover the inference that has been sold."""
    out = {name: micros_to_usd(balance_of(c, name)) for name in CHART}
    row = c.execute(
        "SELECT COALESCE(SUM(CASE direction WHEN 'debit' THEN amount_micros "
        "ELSE -amount_micros END), 0) AS net FROM ledger_entries"
    ).fetchone()
    residual = int(row["net"] if isinstance(row, sqlite3.Row) else row[0])
    return {
        "accounts": out,
        # Must be exactly 0. Anything else means a posting bypassed `post()`.
        "balanced": residual == 0,
        "residual_usd": micros_to_usd(residual),
        # Gross margin so far: what we recognised minus what the inference actually cost.
        "gross_margin_usd": round(
            out["platform_revenue"] + out["breakage_revenue"]
            - out["provider_cost"] - out["processing_fees"]
            - out["creator_cost"] - out["promotional_cost"], 6
        ),
    }
