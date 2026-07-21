"""Platform Accounts service — the State plane's first brick.

This is OUR crown-jewel identity + metering store, deliberately SEPARATE from the daemon
(one accounts store is shared by the whole daemon fleet) and SEPARATE from the Model Gateway
(LiteLLM meters model calls; WE own accounts, budgets, and the spend ledger — the billing
source of truth, so it never becomes LiteLLM's internal DB).

Local now (SQLite, one file), the SAME shape graduates to a hosted service backed by Postgres
(and, later, Cognito/JWT for sign-in) — the daemon only ever sees the /resolve + /budget + /usage
contract, so swapping the backing store never touches agentd.

Contract (what agentd depends on):
    POST /signup   {email, password, budget_usd?}        -> {account_id}
    POST /login    {email, password}                     -> {token, account_id, email}
    GET  /resolve  (Authorization: Bearer <token>)       -> {account_id, email, budget_usd}
    GET  /budget/{account_id}                            -> {budget_usd, spent_usd, remaining, over}
    POST /usage    {account_id, model, in_tokens, out_tokens, cost_usd}
                                                         -> {ok, spent_usd, over}
    GET  /health                                         -> {ok: true}

A session token is the browser's credential; it is NOT a model key. agentd resolves the token
to an account and meters that account's spend — the model key (the gateway master key, or a
per-account virtual key later) never leaves the server side.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# --- storage -----------------------------------------------------------------

DB_PATH = Path(os.environ.get("AGENTD_ACCOUNTS_DB", str(Path(__file__).parent / "data" / "accounts.db")))
_PBKDF2_ROUNDS = 200_000


def _now() -> float:
    return time.time()


def _month_key(ts: float) -> str:
    """Billing period bucket 'YYYY-MM' (UTC) — budgets are per calendar month."""
    return time.strftime("%Y-%m", time.gmtime(ts))


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """One short-lived connection per call (SQLite connect is cheap; endpoints run in a
    threadpool so this never blocks the event loop). WAL keeps readers off writers."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db() -> None:
    with _db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id           TEXT PRIMARY KEY,
                email        TEXT UNIQUE NOT NULL,
                pw_salt      TEXT NOT NULL,
                pw_hash      TEXT NOT NULL,
                budget_usd   REAL,               -- NULL = unlimited
                active       INTEGER NOT NULL DEFAULT 1,
                created_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token        TEXT PRIMARY KEY,
                account_id   TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                created_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id   TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                ts           REAL NOT NULL,
                month        TEXT NOT NULL,       -- 'YYYY-MM' bucket for fast period sums
                model        TEXT NOT NULL DEFAULT '',
                in_tokens    INTEGER NOT NULL DEFAULT 0,
                out_tokens   INTEGER NOT NULL DEFAULT 0,
                cost_usd     REAL NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS ix_usage_acct_month ON usage(account_id, month);
            """
        )


# --- password hashing (stdlib, no bcrypt dependency) -------------------------


def _hash_pw(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ROUNDS)
    return dk.hex()


def _make_pw(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16).hex()
    return salt, _hash_pw(password, salt)


def _verify_pw(password: str, salt: str, expected: str) -> bool:
    return secrets.compare_digest(_hash_pw(password, salt), expected)


# --- spend ledger ------------------------------------------------------------


def _spent_this_month(c: sqlite3.Connection, account_id: str) -> float:
    row = c.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS s FROM usage WHERE account_id=? AND month=?",
        (account_id, _month_key(_now())),
    ).fetchone()
    return float(row["s"] or 0.0)


def _budget_view(c: sqlite3.Connection, account_id: str) -> dict:
    row = c.execute("SELECT budget_usd FROM accounts WHERE id=?", (account_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown account")
    budget = row["budget_usd"]
    spent = _spent_this_month(c, account_id)
    remaining = None if budget is None else max(0.0, budget - spent)
    over = bool(budget is not None and spent >= budget)
    return {
        "account_id": account_id,
        "budget_usd": budget,
        "spent_usd": round(spent, 6),
        "remaining_usd": None if remaining is None else round(remaining, 6),
        "over": over,
        "period": _month_key(_now()),
    }


# --- app ---------------------------------------------------------------------

app = FastAPI(title="agentd accounts", version="0.1.0")
# Local dev: the web client (a different origin, e.g. :5273) signs in via fetch.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tightened to the real web origin in the hosted deploy
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    _init_db()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "accounts"}


@app.post("/signup")
def signup(payload: dict = Body(...)) -> dict:
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="valid email required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="password must be at least 6 characters")
    budget = payload.get("budget_usd")
    budget_val = None if budget in (None, "") else float(budget)
    salt, pw_hash = _make_pw(password)
    account_id = "acct_" + secrets.token_hex(8)
    with _db() as c:
        exists = c.execute("SELECT 1 FROM accounts WHERE email=?", (email,)).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="email already registered")
        c.execute(
            "INSERT INTO accounts (id, email, pw_salt, pw_hash, budget_usd, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (account_id, email, salt, pw_hash, budget_val, _now()),
        )
    return {"account_id": account_id, "email": email, "budget_usd": budget_val}


@app.post("/login")
def login(payload: dict = Body(...)) -> dict:
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    with _db() as c:
        row = c.execute(
            "SELECT id, pw_salt, pw_hash, active FROM accounts WHERE email=?", (email,)
        ).fetchone()
        if row is None or not row["active"] or not _verify_pw(password, row["pw_salt"], row["pw_hash"]):
            raise HTTPException(status_code=401, detail="invalid email or password")
        token = "sess_" + secrets.token_urlsafe(32)
        c.execute(
            "INSERT INTO sessions (token, account_id, created_at) VALUES (?, ?, ?)",
            (token, row["id"], _now()),
        )
    return {"token": token, "account_id": row["id"], "email": email}


def _account_for_token(c: sqlite3.Connection, token: str) -> sqlite3.Row:
    row = c.execute(
        "SELECT a.id AS id, a.email AS email, a.budget_usd AS budget_usd, a.active AS active "
        "FROM sessions s JOIN accounts a ON a.id = s.account_id WHERE s.token=?",
        (token,),
    ).fetchone()
    if row is None or not row["active"]:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return row


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization[len("Bearer ") :].strip()


@app.get("/resolve")
def resolve(authorization: str | None = Header(default=None)) -> dict:
    """agentd calls this at the connection gate: a session token -> the account behind it.
    Returns identity + the account's budget (so agentd can pre-check before a turn)."""
    token = _bearer(authorization)
    with _db() as c:
        row = _account_for_token(c, token)
        view = _budget_view(c, row["id"])
    return {
        "account_id": row["id"],
        "email": row["email"],
        "budget_usd": row["budget_usd"],
        "spent_usd": view["spent_usd"],
        "over": view["over"],
    }


@app.get("/budget/{account_id}")
def budget(account_id: str) -> dict:
    with _db() as c:
        return _budget_view(c, account_id)


@app.post("/usage")
def usage(payload: dict = Body(...)) -> dict:
    """agentd reports a completed model call's cost here (the spend ledger). Returns the new
    month-to-date spend and whether the account is now over its cap."""
    account_id = (payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id required")
    cost = float(payload.get("cost_usd") or 0.0)
    in_tok = int(payload.get("in_tokens") or 0)
    out_tok = int(payload.get("out_tokens") or 0)
    model = (payload.get("model") or "").strip()
    ts = _now()
    with _db() as c:
        if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="unknown account")
        c.execute(
            "INSERT INTO usage (account_id, ts, month, model, in_tokens, out_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (account_id, ts, _month_key(ts), model, in_tok, out_tok, cost),
        )
        view = _budget_view(c, account_id)
    return {"ok": True, "spent_usd": view["spent_usd"], "over": view["over"], "remaining_usd": view["remaining_usd"]}
