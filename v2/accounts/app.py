"""Platform Accounts service — the State plane's first brick.

This is OUR crown-jewel identity + metering store, deliberately SEPARATE from the daemon
(one accounts store is shared by the whole daemon fleet) and SEPARATE from the Model Proxy
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
to an account and meters that account's spend — the model key (the model-proxy master key, or a
per-account virtual key later) never leaves the server side.

Public-exposure hardening (all env-driven; unset = today's open local-dev behavior):
    ACCOUNTS_SESSION_TTL_DAYS  sessions expire after N days (default 30; 0 = never)
    ACCOUNTS_INTERNAL_KEY      when set: /usage requires X-Internal-Key (the ledger is written
                               by trusted infra — the model proxy's callback — only), and
                               /budget/{id} requires the key OR the account's own session token
    ACCOUNTS_CORS_ORIGINS      comma-separated allowed origins (default "*", local dev)
    ACCOUNTS_RATE_LIMIT        per-IP fixed window "count/seconds" on /signup + /login
                               (default "10/60"; "0/0" disables)
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

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# Shared instrumentation (v2/monitoring). Optional at import so an image that has not installed
# it still boots — telemetry must never be able to take down the identity service.
try:
    from agentd_telemetry import count, setup_logging, timing

    _TELEMETRY = True
except ImportError:  # pragma: no cover
    _TELEMETRY = False

    def count(*_a, **_k):  # type: ignore[misc]
        pass

    def timing(*_a, **_k):  # type: ignore[misc]
        pass

    def setup_logging(*_a, **_k):  # type: ignore[misc]
        pass


setup_logging("accounts")

# --- storage -----------------------------------------------------------------

DB_PATH = Path(os.environ.get("AGENTD_ACCOUNTS_DB", str(Path(__file__).parent / "data" / "accounts.db")))
_PBKDF2_ROUNDS = 200_000
_MIN_PASSWORD_LEN = 8


def _session_ttl_s() -> float:
    """Session lifetime in seconds; 0 = never expire (read per-call so tests can flip it)."""
    try:
        days = float(os.environ.get("ACCOUNTS_SESSION_TTL_DAYS", "30") or 30)
    except ValueError:
        days = 30.0
    return days * 86_400


def _internal_key() -> str:
    return os.environ.get("ACCOUNTS_INTERNAL_KEY", "").strip()


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
                cost_usd     REAL NOT NULL DEFAULT 0,
                run_id       TEXT NOT NULL DEFAULT '',  -- the message this call belongs to
                turn_id      TEXT NOT NULL DEFAULT '',  -- which turn WITHIN that message
                credits      INTEGER NOT NULL DEFAULT 0,     -- what the USER was charged
                funding_source TEXT NOT NULL DEFAULT '',     -- which pocket paid
                agent_id     TEXT NOT NULL DEFAULT '',       -- WHICH AGENT burned this
                model_tier   TEXT NOT NULL DEFAULT '',
                cached_tokens INTEGER NOT NULL DEFAULT 0     -- cache reads ~10% of input price
            );
            -- Only columns that the ORIGINAL usage table shipped with may be indexed here;
            -- everything added later is indexed after the migration below. See the note there.
            CREATE INDEX IF NOT EXISTS ix_usage_acct_month ON usage(account_id, month);

            -- PREPAID CREDITS. One row per grant, not one balance per account, because a grant
            -- is what carries an EXPIRY and a CLASS -- and both are load-bearing:
            --   * expiry: an allowance that never expires is an open-ended liability against
            --     money already paid out to a creator, and it makes breakage unmeasurable.
            --   * class: promotional credits must never be revenue-shareable. Free credits plus
            --     a creator payout is a money printer.
            -- scope is 'platform' (spendable on any agent) or 'agent:<id>' (that agent only),
            -- which is what lets a paid agent bundle its own allowance while free agents draw
            -- from the shared pool.
            CREATE TABLE IF NOT EXISTS credit_grants (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id     TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                scope          TEXT NOT NULL DEFAULT 'platform',
                credits        INTEGER NOT NULL DEFAULT 0,
                credits_used   INTEGER NOT NULL DEFAULT 0,
                credit_class   TEXT NOT NULL DEFAULT 'paid',      -- paid | promotional
                model_tier_max TEXT NOT NULL DEFAULT '',          -- '' = any tier allowed
                expires_at     REAL NOT NULL DEFAULT 0,           -- 0 = never
                created_at     REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_grants_acct ON credit_grants(account_id, scope);
            """
        )
        # Additive migration for databases created before correlation IDs existed. SQLite has
        # no ADD COLUMN IF NOT EXISTS, so ask the table what it already has.
        have = {r["name"] for r in c.execute("PRAGMA table_info(usage)")}
        for column in ("run_id", "turn_id"):
            if column not in have:
                c.execute(f"ALTER TABLE usage ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if "credits" not in have:
            c.execute("ALTER TABLE usage ADD COLUMN credits INTEGER NOT NULL DEFAULT 0")
        if "funding_source" not in have:
            c.execute("ALTER TABLE usage ADD COLUMN funding_source TEXT NOT NULL DEFAULT ''")
        if "agent_id" not in have:
            c.execute("ALTER TABLE usage ADD COLUMN agent_id TEXT NOT NULL DEFAULT ''")
        if "model_tier" not in have:
            c.execute("ALTER TABLE usage ADD COLUMN model_tier TEXT NOT NULL DEFAULT ''")
        if "cached_tokens" not in have:
            c.execute("ALTER TABLE usage ADD COLUMN cached_tokens INTEGER NOT NULL DEFAULT 0")

        # Indexes over migrated columns come LAST, and unconditionally.
        #
        # They CANNOT live in the schema script above: that script creates `usage` only
        # IF NOT EXISTS, so against a database written by an older build the CREATE TABLE is
        # a no-op and an index on a column that build never had raises "no such column" —
        # which is a startup crash, not a degraded feature. IF NOT EXISTS makes each one
        # idempotent, so this is also correct for a database created fresh a moment ago.
        # ANY future column added by the migration above must be indexed HERE, not there.
        c.executescript(
            """
            CREATE INDEX IF NOT EXISTS ix_usage_agent ON usage(agent_id, month);
            CREATE INDEX IF NOT EXISTS ix_usage_run   ON usage(run_id);
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
# Browsers (the web client, a different origin) sign in via fetch. Local dev default is open;
# the hosted deploy sets ACCOUNTS_CORS_ORIGINS to the real web origin(s).
_cors = [o.strip() for o in os.environ.get("ACCOUNTS_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- per-IP rate limiting (signup/login only) --------------------------------
# Fixed window, in-process: one container serves the whole free tier, so a dict is enough.
# Behind the ALB the client IP is the first X-Forwarded-For hop.

_rate_hits: dict[str, tuple[int, float]] = {}  # ip -> (count, window_start)


def _rate_limit_cfg() -> tuple[int, float]:
    raw = os.environ.get("ACCOUNTS_RATE_LIMIT", "10/60")
    try:
        count_s, per_s = raw.split("/", 1)
        return int(count_s), float(per_s)
    except (ValueError, AttributeError):
        return 10, 60.0


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _check_rate(request: Request) -> None:
    count, per = _rate_limit_cfg()
    if count <= 0 or per <= 0:
        return
    ip = _client_ip(request)
    now = _now()
    hits, start = _rate_hits.get(ip, (0, now))
    if now - start >= per:
        hits, start = 0, now
    hits += 1
    _rate_hits[ip] = (hits, start)
    if len(_rate_hits) > 10_000:  # bound memory under address churn
        _rate_hits.clear()
    if hits > count:
        raise HTTPException(status_code=429, detail="too many attempts; try again later")


@app.on_event("startup")
def _startup() -> None:
    _init_db()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "accounts"}


@app.post("/signup")
def signup(request: Request, payload: dict = Body(...)) -> dict:
    _check_rate(request)
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="valid email required")
    if len(password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400, detail=f"password must be at least {_MIN_PASSWORD_LEN} characters"
        )
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
def login(request: Request, payload: dict = Body(...)) -> dict:
    # If nobody can sign in, nothing else about the platform matters — so this ratio is one of
    # the handful of numbers on the morning dashboard.
    _check_rate(request)
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    started = time.perf_counter()
    with _db() as c:
        row = c.execute(
            "SELECT id, pw_salt, pw_hash, active FROM accounts WHERE email=?", (email,)
        ).fetchone()
        if row is None or not row["active"] or not _verify_pw(password, row["pw_salt"], row["pw_hash"]):
            count("login_total", outcome="rejected")
            raise HTTPException(status_code=401, detail="invalid email or password")
        token = "sess_" + secrets.token_urlsafe(32)
        c.execute(
            "INSERT INTO sessions (token, account_id, created_at) VALUES (?, ?, ?)",
            (token, row["id"], _now()),
        )
    count("login_total", outcome="ok", _props={"account_id": row["id"]})
    # PBKDF2 at 200k rounds is deliberately slow; watch it so a future rounds bump doesn't
    # quietly turn sign-in into a timeout.
    timing("login_ms", (time.perf_counter() - started) * 1000, outcome="ok")
    return {"token": token, "account_id": row["id"], "email": email}


def _account_for_token(c: sqlite3.Connection, token: str) -> sqlite3.Row:
    row = c.execute(
        "SELECT a.id AS id, a.email AS email, a.budget_usd AS budget_usd, a.active AS active, "
        "s.created_at AS session_created_at "
        "FROM sessions s JOIN accounts a ON a.id = s.account_id WHERE s.token=?",
        (token,),
    ).fetchone()
    if row is None or not row["active"]:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    ttl = _session_ttl_s()
    if ttl > 0 and _now() - float(row["session_created_at"] or 0) > ttl:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))
        # _db() commits only on a CLEAN exit, and we are about to raise — so the purge must be
        # committed here or it rolls back and every expired session stays in the table forever.
        c.commit()
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
    # THE HOT PATH. The Model Proxy calls this before every uncached model call, so this
    # endpoint's latency is added to every user's every message, and its availability gates
    # the entire platform (plan DEF-6). Measured from BOTH sides: here, and at the proxy.
    started = time.perf_counter()
    try:
        token = _bearer(authorization)
        with _db() as c:
            row = _account_for_token(c, token)
            view = _budget_view(c, row["id"])
    except HTTPException:
        count("resolve_total", outcome="rejected")
        timing("resolve_ms", (time.perf_counter() - started) * 1000, outcome="rejected")
        raise
    count("resolve_total", outcome="ok", _props={"account_id": row["id"]})
    timing("resolve_ms", (time.perf_counter() - started) * 1000, outcome="ok")
    return {
        "account_id": row["id"],
        "email": row["email"],
        "budget_usd": row["budget_usd"],
        "spent_usd": view["spent_usd"],
        "over": view["over"],
    }


def _require_internal(x_internal_key: str | None) -> bool:
    """True when the caller presented the internal service key. No key configured (local
    dev) => everything is trusted, today's behavior."""
    configured = _internal_key()
    if not configured:
        return True
    return bool(x_internal_key) and secrets.compare_digest(x_internal_key, configured)


@app.get("/budget/{account_id}")
def budget(
    account_id: str,
    authorization: str | None = Header(default=None),
    x_internal_key: str | None = Header(default=None),
) -> dict:
    """Trusted infra (internal key) or the account's OWN session token may read its budget."""
    if not _require_internal(x_internal_key):
        token = _bearer(authorization)
        with _db() as c:
            row = _account_for_token(c, token)
            if row["id"] != account_id:
                raise HTTPException(status_code=403, detail="not your account")
            return _budget_view(c, account_id)
    with _db() as c:
        return _budget_view(c, account_id)


# --- prepaid credits: resolve / debit / grant --------------------------------
#
# The proxy asks "can this account afford a call, and on which models?" BEFORE the provider is
# touched, then debits AFTER. Both live here because accounts owns the money, and the proxy is
# the only caller that cannot be bypassed by a user-controlled desktop.


def _live_grants(c: sqlite3.Connection, account_id: str, agent_id: str) -> list[sqlite3.Row]:
    """Unexpired grants with credits left, spendable on this agent, SOONEST-EXPIRING FIRST.

    Draining the soonest-expiring grant first is use-it-or-lose-it: it maximises what the user
    actually gets to spend, and it means breakage is genuine non-use rather than an artefact of
    which row we happened to pick.
    """
    scopes = ["platform"] + ([f"agent:{agent_id}"] if agent_id else [])
    now = _now()
    rows = c.execute(
        "SELECT * FROM credit_grants WHERE account_id=? AND credits > credits_used "
        "AND (expires_at = 0 OR expires_at > ?) "
        f"AND scope IN ({','.join('?' * len(scopes))}) "
        "ORDER BY CASE WHEN expires_at = 0 THEN 1 ELSE 0 END, expires_at ASC, id ASC",
        (account_id, now, *scopes),
    ).fetchall()
    return list(rows)


def _funding_view(c: sqlite3.Connection, account_id: str, agent_id: str) -> dict:
    grants = _live_grants(c, account_id, agent_id)
    remaining = sum(int(g["credits"]) - int(g["credits_used"]) for g in grants)
    # The tier ceiling comes from the grant we would spend FIRST, so a cheap-models-only
    # promotional grant cannot be dodged by also holding an unrestricted one.
    tier_max = str(grants[0]["model_tier_max"] or "") if grants else ""
    source = ""
    if grants:
        source = "agent_subscription" if str(grants[0]["scope"]).startswith("agent:") else "platform_pool"
    return {
        "account_id": account_id,
        "credits_remaining": remaining,
        "model_tier_max": tier_max,
        "funding_source": source,
        "credit_class": str(grants[0]["credit_class"]) if grants else "",
    }


@app.get("/funding")
def funding(
    account_id: str,
    agent_id: str = "",
    x_internal_key: str | None = Header(default=None),
) -> dict:
    """What this account can spend right now, and on which model tier. Read by the proxy before
    every uncached call — so it is on the hot path and must stay a single indexed query."""
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    with _db() as c:
        if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="unknown account")
        return _funding_view(c, account_id, agent_id)


@app.post("/debit")
def debit(payload: dict = Body(...), x_internal_key: str | None = Header(default=None)) -> dict:
    """Consume credits. HARD STOP: never goes negative, never partially debits.

    No overdraft is not a policy choice, it is what makes the whole model work. Because the
    allowance is capped, the worst case cost of a subscription is knowable on day one — which
    is what allows paying a creator immediately while inference is still to come. Allow an
    overdraft and one heavy user erases the margin from twenty others.
    """
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    account_id = (payload.get("account_id") or "").strip()
    credits = max(0, int(payload.get("credits") or 0))
    agent_id = (payload.get("agent_id") or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id required")
    with _db() as c:
        grants = _live_grants(c, account_id, agent_id)
        available = sum(int(g["credits"]) - int(g["credits_used"]) for g in grants)
        if credits > available:
            count("debit_total", outcome="insufficient")
            raise HTTPException(
                status_code=402,
                detail=f"insufficient credits: need {credits}, have {available}",
            )
        left = credits
        for g in grants:
            if left <= 0:
                break
            take = min(left, int(g["credits"]) - int(g["credits_used"]))
            c.execute(
                "UPDATE credit_grants SET credits_used = credits_used + ? WHERE id=?",
                (take, g["id"]),
            )
            left -= take
        view = _funding_view(c, account_id, agent_id)
    count("debit_total", outcome="ok")
    # The single number that says "are we selling faster than we are serving?"
    count("credits_consumed_total", credits, _props={"account_id": account_id})
    return {"ok": True, **view}


@app.post("/grant")
def grant(payload: dict = Body(...), x_internal_key: str | None = Header(default=None)) -> dict:
    """Add credits to an account. THE MOCKED PURCHASE.

    Real payments are deliberately out of scope (see the plan's NullPaymentProvider seam), but
    the accounting is not: money history cannot be backfilled, so grants, expiry, class and
    consumption are all recorded for real from day one. Swapping a payment rail in later means
    calling this after a successful charge instead of calling it by hand.
    """
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    account_id = (payload.get("account_id") or "").strip()
    credits = max(0, int(payload.get("credits") or 0))
    if not account_id or credits <= 0:
        raise HTTPException(status_code=400, detail="account_id and positive credits required")
    scope = (payload.get("scope") or "platform").strip() or "platform"
    credit_class = (payload.get("credit_class") or "paid").strip()
    tier_max = (payload.get("model_tier_max") or "").strip()
    # Expiry semantics, chosen to FAIL CLOSED: omitted/0 = never expires (an explicit,
    # deliberate choice), positive = N days from now, NEGATIVE = already expired. Letting a
    # negative fall through to "never" would mean a bad caller value silently mints an immortal
    # grant — the most permissive outcome from the least trustworthy input.
    days = float(payload.get("expires_days") or 0)
    expires_at = (_now() + days * 86_400) if days != 0 else 0.0
    with _db() as c:
        if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="unknown account")
        c.execute(
            "INSERT INTO credit_grants (account_id, scope, credits, credits_used, credit_class, "
            "model_tier_max, expires_at, created_at) VALUES (?, ?, ?, 0, ?, ?, ?, ?)",
            (account_id, scope, credits, credit_class, tier_max, expires_at, _now()),
        )
        view = _funding_view(c, account_id, "")
    count("credits_granted_total", credits, credit_class=credit_class, _props={"account_id": account_id})
    return {"ok": True, **view}


@app.post("/usage")
def usage(payload: dict = Body(...), x_internal_key: str | None = Header(default=None)) -> dict:
    """A completed model call's cost lands here (the spend ledger). TRUSTED WRITERS ONLY —
    in the platform topology that is the model proxy's success callback, which sees every
    call server-side; clients (desktop daemons) cannot write their own ledger. Returns the
    new month-to-date spend and whether the account is now over its cap."""
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    account_id = (payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id required")
    cost = float(payload.get("cost_usd") or 0.0)
    in_tok = int(payload.get("in_tokens") or 0)
    out_tok = int(payload.get("out_tokens") or 0)
    model = (payload.get("model") or "").strip()
    # The tracking number, forwarded by the proxy. Storing it here is what lets one search
    # join a support question ("my chat froze") to what it actually cost.
    run_id = str(payload.get("run_id") or "").strip()[:64]
    turn_id = str(payload.get("turn_id") or "").strip()[:80]
    # What the USER was charged, alongside what the CALL cost us. Both, on the same row: the
    # gap between them is the margin, and it is the number the business lives on.
    credits = max(0, int(payload.get("credits") or 0))
    funding_source = str(payload.get("funding_source") or "").strip()[:32]
    # WHICH AGENT spent this. Needed to price a marketplace listing, to spot an inefficient
    # agent, and eventually to pay its creator — none of which "cost per account" can answer.
    agent_id = str(payload.get("agent_id") or "").strip()[:64]
    model_tier = str(payload.get("model_tier") or "").strip()[:32]
    cached_tok = max(0, int(payload.get("cached_tokens") or 0))
    ts = _now()
    with _db() as c:
        if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            count("ledger_row_total", outcome="rejected", reason="unknown_account")
            raise HTTPException(status_code=404, detail="unknown account")
        c.execute(
            "INSERT INTO usage (account_id, ts, month, model, in_tokens, out_tokens, cost_usd, "
            "run_id, turn_id, credits, funding_source, agent_id, model_tier, cached_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, ts, _month_key(ts), model, in_tok, out_tok, cost, run_id, turn_id,
             credits, funding_source, agent_id, model_tier, cached_tok),
        )
        view = _budget_view(c, account_id)
    # Written from the LEDGER's own side. The proxy counts its attempts; this counts what
    # actually landed. Two independent counters — a gap between them is the interesting signal.
    count("ledger_row_total", outcome="ok", _props={"account_id": account_id, "run_id": run_id})
    if view["over"]:
        count("budget_exceeded_total", _props={"account_id": account_id})
    return {"ok": True, "spent_usd": view["spent_usd"], "over": view["over"], "remaining_usd": view["remaining_usd"]}
