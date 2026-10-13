"""Live welcome-credit policy. Bootstrap settings seed once; admin edits survive restarts."""

import math
import os

MAX_CREDITS = 1_000_000_000
MAX_EXPIRY_DAYS = 3650


def schema(c, *, now: float) -> None:
    # These types/constraints work on both SQLite and Postgres. The caller owns the transaction.
    c.execute("CREATE TABLE IF NOT EXISTS signup_credit_policy ("
              "id INTEGER PRIMARY KEY CHECK (id = 1), enabled INTEGER NOT NULL, "
              "credits BIGINT NOT NULL, expiry_days DOUBLE PRECISION NOT NULL, "
              "updated_at DOUBLE PRECISION NOT NULL, updated_by TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS signup_credit_claims ("
              "account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE, "
              "created_at DOUBLE PRECISION NOT NULL, credits BIGINT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS signup_credit_policy_audit ("
              "enabled INTEGER NOT NULL, credits BIGINT NOT NULL, expiry_days DOUBLE PRECISION NOT NULL, "
              "updated_at DOUBLE PRECISION NOT NULL, updated_by TEXT NOT NULL)")
    try:
        credits = min(MAX_CREDITS, max(0, int(os.environ.get("AGENTD_SIGNUP_CREDITS", "0"))))
    except ValueError:
        credits = 0
    try:
        days = float(os.environ.get("AGENTD_SIGNUP_CREDIT_DAYS", "30"))
        if not math.isfinite(days) or not 0 <= days <= MAX_EXPIRY_DAYS:
            days = 30.0
    except ValueError:
        days = 30.0
    c.execute("INSERT INTO signup_credit_policy "
              "(id, enabled, credits, expiry_days, updated_at, updated_by) "
              "VALUES (1, ?, ?, ?, ?, 'bootstrap') ON CONFLICT (id) DO NOTHING",
              (int(credits > 0), credits or 100_000, days, now))


def read(c) -> dict:
    row = c.execute("SELECT enabled, credits, expiry_days, updated_at, updated_by "
                    "FROM signup_credit_policy WHERE id = 1").fetchone()
    return {**dict(row), "enabled": bool(row["enabled"])}


def save(c, payload: dict, *, now: float, actor: str) -> dict:
    enabled, credits, days = (payload.get(k) for k in ("enabled", "credits", "expiry_days"))
    if type(enabled) is not bool:
        raise ValueError("enabled must be true or false")
    if type(credits) is not int or not 1 <= credits <= MAX_CREDITS:
        raise ValueError(f"credits must be a whole number between 1 and {MAX_CREDITS:,}")
    if type(days) not in (int, float) or not math.isfinite(days) or not 0 <= days <= MAX_EXPIRY_DAYS:
        raise ValueError(f"expiry_days must be between 0 and {MAX_EXPIRY_DAYS}; 0 means no expiry")
    c.execute("UPDATE signup_credit_policy SET enabled=?, credits=?, expiry_days=?, "
              "updated_at=?, updated_by=? WHERE id=1", (int(enabled), credits, days, now, actor))
    c.execute("INSERT INTO signup_credit_policy_audit "
              "(enabled, credits, expiry_days, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)",
              (int(enabled), credits, days, now, actor))
    return read(c)


def claim(c, account_id: str, *, now: float) -> tuple[int, float]:
    """Consume eligibility once, even while disabled. Never backfill on a later login.

    Credits are personal, never added to an enterprise organization's shared pool.
    ON CONFLICT makes an accidental/replayed hook harmless, including concurrent calls.
    """
    policy = read(c)
    org_member = c.execute("SELECT 1 FROM org_members WHERE account_id=? AND active=1 LIMIT 1",
                           (account_id,)).fetchone()
    credits = policy["credits"] if policy["enabled"] and not org_member else 0
    row = c.execute("INSERT INTO signup_credit_claims (account_id, created_at, credits) "
                    "VALUES (?, ?, ?) ON CONFLICT (account_id) DO NOTHING RETURNING account_id",
                    (account_id, now, credits)).fetchone()
    if not row or not credits:
        return 0, 0.0
    days = policy["expiry_days"]
    return int(credits), now + days * 86_400 if days else 0.0
