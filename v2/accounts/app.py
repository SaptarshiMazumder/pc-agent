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
    POST /auth/login {email, password}                   -> {access_token, refresh_token, ...}
    POST /login    {email, password}                     -> alias of /auth/login
    GET  /resolve  (Authorization: Bearer <access_token>) -> {account_id, email, budget_usd}
    GET  /budget/{account_id}                            -> {budget_usd, spent_usd, remaining, over}
    POST /usage    {account_id, model, in_tokens, out_tokens, cost_usd}
                                                         -> {ok, spent_usd, over}
    GET  /health                                         -> {ok: true}

An ACCESS TOKEN is the client's credential; it is NOT a model key. It is a short-lived signed
JWT, so the daemon and the model proxy verify it against cached public keys (GET /auth/jwks.json)
instead of asking this service on every call. The model key never leaves the server side.

Identity itself — credentials, tokens, signing keys, external providers — lives in v2/identity/
and is composed here. THIS service owns what an account HAS (budgets, credits, the ledger); that
module owns who someone IS. See identity/__init__.py for why the line is drawn there.

Public-exposure hardening (all env-driven; unset = today's open local-dev behavior):
    ACCOUNTS_INTERNAL_KEY      when set: /usage requires X-Internal-Key (the ledger is written
                               by trusted infra — the model proxy's callback — only), and
                               /budget/{id} requires the key OR the account's own session token
    ACCOUNTS_CORS_ORIGINS      comma-separated allowed origins (default "*", local dev)
    ACCOUNTS_RATE_LIMIT        per-IP fixed window "count/seconds" on /signup + /login
                               and on /me/purchase (default "10/60"; "0/0" disables)

What is for sale is DATA, never code:
    AGENTD_CREDIT_PACKS        JSON list of credit packs; replaces the built-in seed entirely
    AGENTD_CREDIT_PACK_DAYS    how long a purchased pack lasts before it expires (default 365)
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# Shared instrumentation (v2/monitoring). Optional at import so an image that has not installed
# it still boots — telemetry must never be able to take down the identity service.
try:
    from agentd_telemetry import count, gauge, setup_logging, timing

    _TELEMETRY = True
except ImportError:  # pragma: no cover
    _TELEMETRY = False

    def count(*_a, **_k):  # type: ignore[misc]
        pass

    def gauge(*_a, **_k):  # type: ignore[misc]
        pass

    def timing(*_a, **_k):  # type: ignore[misc]
        pass

    def setup_logging(*_a, **_k):  # type: ignore[misc]
        pass


# THE PAYMENT RAIL LIVES IN ITS OWN MODULE (v2/payments/), imported as a package from both the
# image (/app/payments/) and the test run (v2/ is on sys.path). Accounts depends on its
# INTERFACES and its composition root; it never names Stripe, and it never learns how a card
# works. See payments/__init__.py for why the dependency inverts at post-processing.
from payments.application.interfaces.payment_gateway import PurchaseRequest
from payments.application.services.checkout_service import CheckoutService
from payments.application.services.payment_event_service import PaymentEventService
from payments.domain.money import Money
from payments.domain.payment_intent import PaymentIntent
from payments.infrastructure.sqlite_payment_intent_store import SqlitePaymentIntentStore
from payments.main.payment_gateway_factory import (
    build_payment_gateway,
    build_webhook_verifier,
    has_webhook,
)
from payments.presentation.payment_router import build_payment_router

# IDENTITY (v2/identity/) — who a caller is, as opposed to what their account HAS. Imported the
# same way payments is: this service depends on the module's INTERFACES and its composition root,
# and never on an adapter. See identity/__init__.py for why the split is drawn where it is.
#
# Deliberately NOT optional. A service that boots without its auth stack and quietly serves the
# legacy path instead is a service whose security posture depends on whether an import worked.
from identity.domain.errors import (
    AccountDisabled,
    AuthenticationFailed,
    RefreshReuseDetected,
    TokenInvalid,
)
from identity.infrastructure import sqlite_schema as identity_schema
from identity.infrastructure.local_password_provider import PROVIDER_NAME as LOCAL_PROVIDER
from identity.infrastructure.sqlite_identity_link_store import SqliteIdentityLinkStore
from identity.infrastructure.sqlite_refresh_store import SqliteRefreshStore
from identity.main import identity_factory
from identity.presentation.auth_router import build_auth_router

# Sibling modules. A bare import works under uvicorn (WORKDIR /app is on sys.path) but NOT when
# the tests load this file by path, where the module has no package. Same defensive pattern as
# model_proxy/custom_auth.py's `metering` import — and unlike telemetry these are NOT optional:
# the ledger is the money, so failing to import must be a hard startup failure, not a no-op.
try:  # pragma: no cover - exercised by whichever path the runtime takes
    import admin_api
    import ledger
    import orgs_api
    from accounts_post_processor import (
        AccountsPostProcessor,
        PurchaseOrder,
        WebhookPostProcessor,
    )
    from app_secret_loader import AppSecretLoader, AppSecretUnavailable
    from identity_bridge import SqliteAccountDirectory
except ModuleNotFoundError:  # pragma: no cover
    import importlib.util as _ilu
    import pathlib as _pathlib
    import sys as _sys

    def _sibling(name: str):
        spec = _ilu.spec_from_file_location(name, _pathlib.Path(__file__).with_name(f"{name}.py"))
        assert spec and spec.loader
        module = _ilu.module_from_spec(spec)
        # MUST be registered BEFORE exec_module. These modules define dataclasses under
        # `from __future__ import annotations`, and dataclasses resolves those string
        # annotations via sys.modules[cls.__module__] — absent, it dereferences None and the
        # import dies with a bewildering AttributeError from inside the stdlib.
        _sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    admin_api = _sibling("admin_api")
    ledger = _sibling("ledger")
    orgs_api = _sibling("orgs_api")
    _post_processing = _sibling("accounts_post_processor")
    AccountsPostProcessor = _post_processing.AccountsPostProcessor
    PurchaseOrder = _post_processing.PurchaseOrder
    WebhookPostProcessor = _post_processing.WebhookPostProcessor
    _secret_loader = _sibling("app_secret_loader")
    AppSecretLoader = _secret_loader.AppSecretLoader
    AppSecretUnavailable = _secret_loader.AppSecretUnavailable
    SqliteAccountDirectory = _sibling("identity_bridge").SqliteAccountDirectory


setup_logging("accounts")

# --- storage -----------------------------------------------------------------

DB_PATH = Path(os.environ.get("AGENTD_ACCOUNTS_DB", str(Path(__file__).parent / "data" / "accounts.db")))
_PBKDF2_ROUNDS = 200_000
_MIN_PASSWORD_LEN = 8


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
            -- NOTE: the `sessions` table is GONE. It held opaque `sess_` credentials, which
            -- were replaced by signed access tokens + rotating refresh tokens (v2/identity).
            -- A database created before that still HAS the table; it is simply never read or
            -- written now. Deliberately not dropped: an irreversible DDL step on live data, in
            -- exchange for reclaiming a few kilobytes, is a bad trade.
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
        # THE DE-DUPLICATION KEY. The proxy buffers a usage row when the write fails and replays
        # it later -- but "failed" includes "accounts committed the row and the response was
        # lost". Without a key the replay inserts a SECOND copy, silently overstating that
        # account's usage in a way nothing detects and nothing can undo.
        if "event_id" not in have:
            c.execute("ALTER TABLE usage ADD COLUMN event_id TEXT NOT NULL DEFAULT ''")
        # WHICH ORG'S POOL a turn drew from ('' = personal — every row that ever existed).
        # On usage it is what makes the org rollup and the per-member cap one indexed query;
        # on credit_grants it is what makes a grant AN ORG'S rather than a person's.
        if "org_id" not in have:
            c.execute("ALTER TABLE usage ADD COLUMN org_id TEXT NOT NULL DEFAULT ''")
        have_grants = {r["name"] for r in c.execute("PRAGMA table_info(credit_grants)")}
        if "org_id" not in have_grants:
            c.execute("ALTER TABLE credit_grants ADD COLUMN org_id TEXT NOT NULL DEFAULT ''")
        # Seat products / org subscriptions. GUARDED ON THE TABLE EXISTING: this migration block
        # runs before the CREATE TABLEs below, so on a fresh database there is nothing to alter —
        # the CREATE TABLE statements carry these columns themselves.
        have_products = {r["name"] for r in c.execute("PRAGMA table_info(products)").fetchall()}
        if have_products and "seats" not in have_products:
            c.execute("ALTER TABLE products ADD COLUMN seats INTEGER NOT NULL DEFAULT 0")
        have_subs = {r["name"] for r in c.execute("PRAGMA table_info(subscriptions)").fetchall()}
        if have_subs and "org_id" not in have_subs:
            c.execute("ALTER TABLE subscriptions ADD COLUMN org_id TEXT NOT NULL DEFAULT ''")

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
            -- Tenant-id-leading, PARTIAL: the org rollup and the per-member cap both start from
            -- org_id, and excluding '' keeps the index empty on a deployment with no orgs.
            CREATE INDEX IF NOT EXISTS ix_usage_org
                ON usage(org_id, month, account_id) WHERE org_id <> '';
            CREATE INDEX IF NOT EXISTS ix_grants_org
                ON credit_grants(org_id) WHERE org_id <> '';
            -- PARTIAL, because every row written before event_id existed carries '' and a
            -- plain UNIQUE index would collide on the second of them. Rows without a key keep
            -- the old at-least-once behaviour; rows with one are exactly-once.
            CREATE UNIQUE INDEX IF NOT EXISTS ix_usage_event
                ON usage(event_id) WHERE event_id <> '';

            -- WHAT IS FOR SALE (2.2). A product is the thing a purchase buys: a platform credit
            -- pack (no creator) or an agent subscription (a creator earns from it). Kept as data
            -- so adding a price or a tier is a row, never a deploy.
            CREATE TABLE IF NOT EXISTS products (
                id             TEXT PRIMARY KEY,
                kind           TEXT NOT NULL DEFAULT 'credit_pack',  -- credit_pack | agent_subscription
                title          TEXT NOT NULL DEFAULT '',
                creator_id     TEXT NOT NULL DEFAULT '',   -- '' = platform's own, nobody accrues
                agent_id       TEXT NOT NULL DEFAULT '',
                price_usd      REAL NOT NULL DEFAULT 0,
                credits        INTEGER NOT NULL DEFAULT 0, -- 0 = derive from price and markup
                scope          TEXT NOT NULL DEFAULT 'platform',
                model_tier_max TEXT NOT NULL DEFAULT '',
                period_days    INTEGER NOT NULL DEFAULT 30,
                active         INTEGER NOT NULL DEFAULT 1,
                created_at     REAL NOT NULL DEFAULT 0,
                seats          INTEGER NOT NULL DEFAULT 0  -- seat products only
            );

            -- A recurring intent to buy. Renewal is NOT automatic yet: this records what should
            -- renew and when, so a scheduler can be added without a schema change.
            CREATE TABLE IF NOT EXISTS subscriptions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id   TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                product_id   TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'active',   -- active | cancelled
                renews_at    REAL NOT NULL DEFAULT 0,
                created_at   REAL NOT NULL,
                org_id       TEXT NOT NULL DEFAULT ''  -- the org a seat/pool subscription funds
            );
            CREATE INDEX IF NOT EXISTS ix_subs_acct ON subscriptions(account_id, status);
            -- One subscription per (account, product): renewing must UPDATE the existing row,
            -- not accumulate a new one each period, or renew-due would charge N times over.
            CREATE UNIQUE INDEX IF NOT EXISTS ix_subs_acct_product
                ON subscriptions(account_id, product_id);
            CREATE INDEX IF NOT EXISTS ix_subs_due ON subscriptions(status, renews_at);

            -- WHO MAY RUN WHAT (2.5). Separate from credits on purpose: having money is not the
            -- same as being allowed. This is also where "this agent requires BYOK" will live.
            CREATE TABLE IF NOT EXISTS entitlements (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id   TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                agent_id     TEXT NOT NULL,
                source       TEXT NOT NULL DEFAULT 'purchase',  -- purchase | grant | trial
                min_version  TEXT NOT NULL DEFAULT '',
                expires_at   REAL NOT NULL DEFAULT 0,           -- 0 = never
                created_at   REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ix_ent_acct_agent
                ON entitlements(account_id, agent_id);

            -- WHO MAY ADMINISTER THE PLATFORM. Separate from every other table here because being
            -- an admin is not something an account HAS (credits, entitlements) -- it is something
            -- it IS, and it is the only row in this database that grants power over other people's
            -- accounts.
            --
            -- THIS TABLE IS NOT THE ONLY SOURCE. `AGENTD_ADMIN_IDENTITIES` (deploy config) is
            -- checked as well, and deliberately CANNOT be demoted from the dashboard -- see
            -- _is_admin for why that break-glass exists and why seeding rows from it would be
            -- worse than reading it directly.
            CREATE TABLE IF NOT EXISTS admins (
                account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                email      TEXT NOT NULL DEFAULT '',
                added_by   TEXT NOT NULL DEFAULT '',
                added_at   REAL NOT NULL DEFAULT 0,
                active     INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        ledger.schema(c)
        SqlitePaymentIntentStore.create_schema(c)
        # Identity's tables, versioned by identity itself (see its sqlite_schema for why it does
        # NOT use the PRAGMA table_info pattern above). Then the backfill that makes this change
        # non-destructive: every account that existed before identity gets a `local` identity row,
        # so its next login resolves to the SAME account id instead of minting a second one.
        identity_schema.create_schema(c)
        identity_schema.backfill_local_identities(c, at=_now())


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
# Credentials are ON because the web client's session is an HttpOnly cookie now (auth_router.py).
# A wildcard origin cannot legally accompany credentials, so the dev default becomes an
# echo-everything regex — same openness, spelled the way browsers accept it. Hosted deploys keep
# setting ACCOUNTS_CORS_ORIGINS to the real origin(s), which is the configuration that matters.
_wildcard = not _cors or _cors == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if _wildcard else _cors,
    allow_origin_regex=".*" if _wildcard else None,
    allow_credentials=True,
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


# --- what is for sale ---------------------------------------------------------
# The catalogue lives in the `products` table, so a price or a tier is a row and never a deploy.
# But an empty table means an empty store on a fresh environment, so the packs below are a SEED:
# they are inserted only when absent, and AGENTD_CREDIT_PACKS replaces the list outright.
#
# Packs are defined by CREDITS, and the price is DERIVED (ledger.usd_for_credits). That direction
# matters: a credit is a unit of service, so a round number of them is the meaningful quantity,
# and deriving the price means the store can never disagree with the markup dial. State
# `price_usd` on a pack only to deliberately break the usual ratio (a launch promotion).
_CREDIT_PACK_SEED: list[dict] = [
    {"id": "credits-1k", "credits": 1_000, "title": "1,000 credits"},
    {"id": "credits-10k", "credits": 10_000, "title": "10,000 credits"},
    {"id": "credits-100k", "credits": 100_000, "title": "100,000 credits"},
    {"id": "credits-1m", "credits": 1_000_000, "title": "1,000,000 credits"},
]


def _credit_pack_days() -> int:
    try:
        return int(os.environ.get("AGENTD_CREDIT_PACK_DAYS", "") or 365)
    except ValueError:
        return 365


def _credit_packs() -> list[dict]:
    """The packs to seed. AGENTD_CREDIT_PACKS (a JSON list) replaces the seed entirely.

    A malformed value falls back to the seed rather than failing to boot: sign-in must not go
    down because a store setting has a typo in it.
    """
    raw = os.environ.get("AGENTD_CREDIT_PACKS", "").strip()
    if not raw:
        return [dict(p) for p in _CREDIT_PACK_SEED]
    try:
        parsed = json.loads(raw)
        packs = [p for p in parsed if isinstance(p, dict) and p.get("id")]
        if not packs:
            raise ValueError("no usable entries")
        return packs
    except (ValueError, TypeError) as e:
        count("config_invalid_total", _props={"setting": "AGENTD_CREDIT_PACKS", "error": str(e)[:120]})
        return [dict(p) for p in _CREDIT_PACK_SEED]


def _seed_credit_packs() -> None:
    """Insert any missing pack. DO NOTHING on conflict, deliberately.

    An upsert here would revert every operator edit made through POST /products on the next
    deploy -- so a price change would silently last only until the next restart. Absent packs are
    created; existing ones are left exactly as they are.
    """
    packs, period = _credit_packs(), _credit_pack_days()
    with _db() as c:
        for p in packs:
            credits = int(p.get("credits") or 0)
            price = float(p.get("price_usd") or 0) or ledger.usd_for_credits(credits)
            if credits <= 0 or price <= 0:
                continue
            c.execute(
                "INSERT INTO products (id, kind, title, creator_id, agent_id, price_usd, "
                "credits, scope, model_tier_max, period_days, active, created_at) "
                "VALUES (?, 'credit_pack', ?, '', '', ?, ?, 'platform', ?, ?, 1, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (str(p["id"]), str(p.get("title") or f"{credits:,} credits"), price, credits,
                 str(p.get("model_tier_max") or ""), int(p.get("period_days") or period), _now()),
            )


#: Seat products, seeded like the credit packs and for the same reason: an org admin opening
#: their org page must find seats FOR SALE, not an empty shelf with a note about environment
#: configuration. AGENTD_SEAT_PACKS (a JSON list: [{"id","seats","price_usd","title"?}]) replaces
#: this list outright; prices here are stated rather than derived because a seat is admission,
#: not a quantity of service the markup dial knows about.
_SEAT_PACK_SEED: list[dict] = [
    {"id": "seats-5", "seats": 5, "price_usd": 25.0, "title": "5 seats"},
    {"id": "seats-10", "seats": 10, "price_usd": 45.0, "title": "10 seats"},
    {"id": "seats-20", "seats": 20, "price_usd": 80.0, "title": "20 seats"},
]


def _seat_packs() -> list[dict]:
    raw = os.environ.get("AGENTD_SEAT_PACKS", "").strip()
    if not raw:
        return [dict(p) for p in _SEAT_PACK_SEED]
    try:
        packs = [dict(p) for p in json.loads(raw) if isinstance(p, dict)]
        if not packs:
            raise ValueError("no usable entries")
        return packs
    except (ValueError, TypeError) as e:
        count("config_invalid_total", _props={"setting": "AGENTD_SEAT_PACKS", "error": str(e)[:120]})
        return [dict(p) for p in _SEAT_PACK_SEED]


def _seed_seat_packs() -> None:
    """Same contract as the credit-pack seed: absent rows are created, existing rows are LEFT
    ALONE, so an operator's price edit survives the next deploy."""
    with _db() as c:
        for p in _seat_packs():
            seats = int(p.get("seats") or 0)
            price = float(p.get("price_usd") or 0)
            if seats <= 0 or price <= 0:
                continue
            c.execute(
                "INSERT INTO products (id, kind, title, creator_id, agent_id, price_usd, "
                "credits, scope, model_tier_max, period_days, active, created_at, seats) "
                "VALUES (?, 'seat_subscription', ?, '', '', ?, 0, 'platform', '', 30, 1, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (str(p["id"]), str(p.get("title") or f"{seats} seats"), price, _now(), seats),
            )


#: What this service reads from the app secret — the same statement of need as its
#: `secret_keys` map in infra/modules/variables.tf, made where the code runs. The vault's
#: OTHER fields (model-provider keys) are deliberately not loaded: this process has no
#: business holding keys it never reads.
_APP_SECRET_FIELDS = (
    "ACCOUNTS_INTERNAL_KEY",
    "AGENTD_IDENTITY_KEK",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "DODO_API_KEY",
    "DODO_WEBHOOK_SECRET",
)


@app.on_event("startup")
def _startup() -> None:
    # SECRETS FIRST, AND MANDATORY. This service reads its secrets from Secrets Manager and
    # nowhere else — the same source on ECS and on a developer's machine, so there is exactly
    # one place a key lives. No secret id means no boot; booting on ambient env vars is the
    # two-sources-of-truth failure this exists to remove. (ECS still injects the same fields
    # at task start; the load below re-reads the same secret and agrees with itself.)
    secret_id = (os.environ.get("AGENTD_APP_SECRET_ID") or "").strip()
    if not secret_id:
        raise AppSecretUnavailable(
            "AGENTD_APP_SECRET_ID is not set. The accounts service reads its secrets from "
            "Secrets Manager and refuses to start without it — locally, point it at the dev "
            "secret (run-local.py does) and have AWS credentials available."
        )
    AppSecretLoader(
        secret_id,
        fields=_APP_SECRET_FIELDS,
        region=(os.environ.get("AWS_REGION") or "").strip(),
    ).load_into_environ()
    _init_db()
    _seed_credit_packs()
    _seed_seat_packs()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "accounts"}


@app.get("/.well-known/agentd-platform")
def platform_discovery() -> dict:
    """WHERE EVERYTHING IS, answered by the deployment itself.

    THE FIX FOR "the same account should work everywhere". Every client used to bake every URL —
    each desktop flavor its own `accounts_url`, the web image its own build arg — and they drifted
    onto three different load balancers. A different stack is a different database, so the same
    email became a different `acct_` id with different credits, silently.

    Now a client bakes ONE value (the platform URL) and reads the rest from here, so there is a
    single place per environment that can be wrong, and it is the same place for every client.

    `issuer` is the load-bearing field: the daemon and the model proxy check it, so a token minted
    by the dev stack is refused BY NAME in production instead of being quietly treated as a
    different account.

    PUBLIC and unauthenticated, necessarily — a client has to be able to read it before it can
    sign in. Everything here is already public knowledge (hostnames), and no secret may ever be
    added to this response.

    `providers` is DATA, so the sign-in UI renders its buttons from this list. Adding Google later
    is a row here plus an adapter — no client release, which is the same rule the rest of the
    codebase follows for models, tools and plugins.
    """
    issuer = identity_factory.issuer()
    return {
        "issuer": issuer,
        "auth_url": _public_url("AGENTD_PUBLIC_ACCOUNTS_URL") or issuer,
        "jwks_uri": f"{issuer}/auth/jwks.json" if issuer else "",
        "ws_url": _public_url("AGENTD_PUBLIC_WS_URL"),
        "model_proxy_url": _public_url("AGENTD_PUBLIC_MODEL_PROXY_URL"),
        # WHAT THE SIGN-IN SCREEN RENDERS, as data. The UI iterates this list; it names no
        # provider itself, so adding Microsoft is four environment variables on this service and
        # zero client changes. Same rule the codebase already follows for models and tools.
        "providers": _providers(),
        "token_auth": identity_factory.tokens_available(),
        "access_ttl_s": identity_factory.access_ttl_s(),
        "service": "accounts",
    }


def _providers() -> list[dict]:
    """Every way to sign in to this deployment: the password form, plus any external provider.

    The password entry is omitted when the primary provider cannot take one (a pure-OIDC
    deployment), so the UI does not render a form that nothing can accept.
    """
    out: list[dict] = []
    primary = identity_factory.configured_provider_name()
    if primary == identity_factory.LOCAL:
        out.append({"id": "local", "label": "Email", "kind": "password"})
    for provider in identity_factory.external_providers():
        out.append(
            {
                "id": provider.name,
                # Title-cased from the configured id, so "google" renders as "Google" without a
                # lookup table that would have to grow for every new provider.
                "label": provider.name.replace("-", " ").title(),
                "kind": "oidc",
            }
        )
    return out


def _public_url(env_name: str) -> str:
    """A URL a BROWSER can reach, from the environment.

    Deliberately separate from the internal service addresses the daemon and proxy use to reach
    each other (`http://accounts.agentd.local:4100` is service-discovery DNS a visitor cannot
    resolve). Publishing the internal one here would produce a discovery document that works
    perfectly from inside the VPC and fails for every actual user.
    """
    return (os.environ.get(env_name, "") or "").strip().rstrip("/")


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
        # AND ITS IDENTITY RECORD, in the same transaction. Without this the account exists but
        # has no `local` login attached, so the token path would not find it and would mint a
        # SECOND account on first sign-in — a new id, an empty balance, and nothing to notice it
        # by. The startup backfill covers accounts created before identity existed; this covers
        # every one created from now on.
        SqliteIdentityLinkStore(c).link(
            provider=LOCAL_PROVIDER, subject=account_id, account_id=account_id, email=email
        )
    return {"account_id": account_id, "email": email, "budget_usd": budget_val}


@app.post("/login")
def login(request: Request, payload: dict = Body(...)) -> dict:
    """Sign in. Compatibility alias for /auth/login — same credential, same response.

    It used to ALSO mint an opaque `sess_` row so that already-shipped clients (which read
    `token`) kept working while the token path rolled out. That dual-issuing is gone: nothing
    reads `token` any more, and one login endpoint that hands out two kinds of credential is two
    things to revoke, two things to expire, and two code paths to keep secure.

    Kept as a route rather than deleted because it is a published address that scripts and the
    client's own signup flow use. New callers should use /auth/login.
    """
    # If nobody can sign in, nothing else about the platform matters — so this ratio is one of
    # the handful of numbers on the morning dashboard.
    _check_rate(request)
    if not identity_factory.tokens_available():
        # A 503 rather than a startup crash: `AGENTD_AUTH_ISSUER` is derived from the stack's
        # public address, which is legitimately empty while a stack is hibernating or has no
        # ALB yet. Refusing to boot then would turn a dormant environment into a broken one.
        raise HTTPException(
            status_code=503,
            detail="this deployment has no identity configured (AGENTD_AUTH_ISSUER is unset)",
        )
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    started = time.perf_counter()
    with _db() as c:
        with _auth_service(c) as service:
            try:
                pair = service.login(
                    email=email,
                    password=password,
                    client_id=str(payload.get("client_id") or "")[:64],
                    device_label=str(payload.get("device_label") or "")[:120],
                )
            except (AuthenticationFailed, AccountDisabled) as e:
                count("login_total", outcome="rejected")
                raise HTTPException(status_code=401, detail="invalid email or password") from e
        # The domain OFFER (tenancy E1): orgs that claim this email's domain, surfaced for the
        # client to render — never a silent auto-add. Same connection, one indexed query.
        joinable = orgs_api.joinable_orgs(c, email, pair.account_id)
    count("login_total", outcome="ok", _props={"account_id": pair.account_id})
    # PBKDF2 at 200k rounds is deliberately slow; watch it so a future rounds bump doesn't
    # quietly turn sign-in into a timeout.
    timing("login_ms", (time.perf_counter() - started) * 1000, outcome="ok")
    out = {**pair.as_response(), "email": email}
    if joinable:
        out["joinable_orgs"] = joinable
    return out


def _org_resolver(conn: sqlite3.Connection):
    """``account_id -> ((org_id, role), ...)`` for the token's ``orgs`` claim, bound to the
    caller's live connection. ONE query implementation (orgs_api.org_memberships) shared with
    every /orgs route, so what a token asserts and what a route enforces cannot drift."""

    def resolve(account_id: str):
        return orgs_api.org_memberships(conn, account_id)

    return resolve


@contextmanager
def _auth_service(conn: sqlite3.Connection | None = None):
    """An AuthService bound to a live connection.

    Built per request, like CheckoutService in _apply_purchase: the identity stores take a
    connection, so a failed login rolls back everything it touched along with the caller's
    transaction. Pass an existing connection to JOIN the caller's transaction rather than opening
    a second one against the same SQLite file — nesting two writers on one file is how a request
    deadlocks against itself.
    """
    if conn is not None:
        yield identity_factory.build_auth_service(
            conn, SqliteAccountDirectory(conn), org_resolver=_org_resolver(conn)
        )
        return
    with _db() as c:
        try:
            yield identity_factory.build_auth_service(
                c, SqliteAccountDirectory(c), org_resolver=_org_resolver(c)
            )
        except RefreshReuseDetected:
            # THE ONE FAILURE THAT MUST STILL COMMIT. _db() commits only on a clean exit, so a
            # raise normally rolls the request back — and here the thing being rolled back is the
            # family revocation, which IS the security response to a stolen refresh token. Without
            # this the service detects the theft, reports it, and then quietly un-revokes the
            # family, leaving the attacker's copy working. Same fix, same reason, as the
            # expired-session purge in _account_for_legacy_session.
            c.commit()
            raise


def _account_for_token(c: sqlite3.Connection, token: str) -> sqlite3.Row:
    """A bearer token -> the account behind it. ONE credential kind: a signed access token.

    There used to be a second branch here for opaque `sess_` sessions, kept while the token path
    rolled out so that already-shipped clients went on working. It is gone, and the endpoint is
    better for it: one kind of credential means one thing to revoke, one thing to expire and one
    code path to keep secure. Nothing outside this repository ever held a `sess_` token.

    THE CLAIMS ARE NOT TRUSTED FOR ANYTHING BUT `sub`. The accounts row is re-read on every call,
    so a token minted ten minutes ago cannot assert a budget or an active flag that has since
    changed — which is what lets a deactivation take effect immediately despite a live token.
    """
    if not token:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    if not identity_factory.tokens_available():
        raise HTTPException(status_code=401, detail="invalid or expired token")
    with _auth_service(c) as service:
        try:
            claims = service.verify_access(token)
        except TokenInvalid as e:
            raise HTTPException(status_code=401, detail=str(e) or "invalid or expired token") from e
    row = c.execute(
        "SELECT id, email, budget_usd, active FROM accounts WHERE id = ?",
        (claims.account_id,),
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
    # THE HOT PATH. The Model Proxy calls this before every uncached model call, so this
    # endpoint's latency is added to every user's every message, and its availability gates
    # the entire platform (plan DEF-6). Measured from BOTH sides: here, and at the proxy.
    started = time.perf_counter()
    try:
        token = _bearer(authorization)
        with _db() as c:
            row = _account_for_token(c, token)
            view = _budget_view(c, row["id"])
            orgs = orgs_api.org_memberships(c, str(row["id"]))
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
        # Same shape as the token's own claim, so a daemon on the HTTP path (no local JWKS)
        # learns membership from the same answer the JWT path decodes locally.
        "orgs": [{"id": org_id, "role": role} for org_id, role in orgs],
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


def _live_grants(
    c: sqlite3.Connection, account_id: str, agent_id: str, org_id: str = ""
) -> list[sqlite3.Row]:
    """Unexpired grants with credits left, spendable on this agent, SOONEST-EXPIRING FIRST.

    Draining the soonest-expiring grant first is use-it-or-lose-it: it maximises what the user
    actually gets to spend, and it means breakage is genuine non-use rather than an artefact of
    which row we happened to pick.

    TWO POCKETS THAT NEVER MIX (tenancy E2). An org-attributed turn (org_id given) draws the
    ORG'S pool and only the org's pool; a personal turn draws only rows with org_id='' — which
    is every row that existed before orgs did, so the personal path is byte-identical. Crossing
    silently in either direction is the failure mode: an employee's own credits funding company
    work by accident, or the company pool leaking into personal chats.
    """
    scopes = ["platform"] + ([f"agent:{agent_id}"] if agent_id else [])
    now = _now()
    if org_id:
        # A SUSPENDED org's pool answers zero everywhere at once — suspension would be
        # decorative if grants kept draining while the routes 404ed.
        where = (
            "org_id = ? AND EXISTS (SELECT 1 FROM orgs o WHERE o.id = org_id AND o.active = 1)"
        )
        args = [org_id]
    else:
        where, args = "account_id = ? AND org_id = ''", [account_id]
    rows = c.execute(
        f"SELECT * FROM credit_grants WHERE {where} AND credits > credits_used "
        "AND (expires_at = 0 OR expires_at > ?) "
        f"AND scope IN ({','.join('?' * len(scopes))}) "
        "ORDER BY CASE WHEN expires_at = 0 THEN 1 ELSE 0 END, expires_at ASC, id ASC",
        (*args, now, *scopes),
    ).fetchall()
    return list(rows)


def _org_cap_left(c: sqlite3.Connection, org_id: str, account_id: str) -> int | None:
    """How much of the member's monthly org allowance remains — None = uncapped.

    The cap is POLICY on the membership row (Lovable/ChatGPT pattern), checked at the same
    funding gate every turn already passes. Month-to-date comes from the usage ledger's own
    org_id column, so the cap and the admin rollup can never disagree about what was spent.
    """
    member = c.execute(
        "SELECT monthly_credit_cap FROM org_members "
        "WHERE org_id = ? AND account_id = ? AND active = 1",
        (org_id, account_id),
    ).fetchone()
    if member is None:
        return 0  # not an (active) member: fail CLOSED — no allowance at all
    cap = int(member["monthly_credit_cap"] or 0)
    if cap <= 0:
        return None
    spent = c.execute(
        "SELECT COALESCE(SUM(credits), 0) AS s FROM usage "
        "WHERE org_id = ? AND account_id = ? AND month = ?",
        (org_id, account_id, _month_key(_now())),
    ).fetchone()
    return max(0, cap - int(spent["s"] or 0))


def _entitlement_state(c: sqlite3.Connection, account_id: str, agent_id: str) -> tuple[bool, bool]:
    """(required, held) for running this agent.

    REQUIRED IS DATA-DRIVEN, never a list in code: an agent needs an entitlement precisely when
    somebody is selling it (an active product names it). So first-party agents, the default
    agent, and anything not on the marketplace stay freely runnable, and putting an agent up for
    sale is what starts gating it — one row, no deploy.
    """
    if not agent_id:
        return False, True
    required = c.execute(
        "SELECT 1 FROM products WHERE agent_id = ? AND active = 1 LIMIT 1", (agent_id,)
    ).fetchone() is not None
    if not required:
        return False, True
    held = c.execute(
        "SELECT 1 FROM entitlements WHERE account_id=? AND agent_id=? "
        "AND (expires_at = 0 OR expires_at > ?) LIMIT 1",
        (account_id, agent_id, _now()),
    ).fetchone() is not None
    return True, held


def _member_org(c: sqlite3.Connection, account_id: str) -> str:
    """The ONE org this account belongs to, or ''.

    One, by rule: joins refuse a second membership (orgs_api._ensure_member), so this is a
    lookup rather than a choice. The rule exists for exactly this call site — with two orgs
    there would be no honest answer to "whose pool pays for this turn"."""
    rows = orgs_api.org_memberships(c, account_id)
    return str(rows[0][0]) if rows else ""


def _funding_view(
    c: sqlite3.Connection, account_id: str, agent_id: str, org_id: str = ""
) -> dict:
    # MEMBERSHIP DECIDES THE POCKET (enterprise rule). An account that belongs to an org has no
    # personal wallet: every turn draws the org's pool, bounded by their seat allowance —
    # whatever agent is running, and whether or not the daemon stamped an org on the turn. The
    # explicit org_id (stamped for org-owned agents) still wins when present, because it also
    # carries attribution.
    if not org_id:
        org_id = _member_org(c, account_id)
    grants = _live_grants(c, account_id, agent_id, org_id)
    remaining = sum(int(g["credits"]) - int(g["credits_used"]) for g in grants)
    # The tier ceiling comes from the grant we would spend FIRST, so a cheap-models-only
    # promotional grant cannot be dodged by also holding an unrestricted one.
    tier_max = str(grants[0]["model_tier_max"] or "") if grants else ""
    source = ""
    if grants:
        source = "agent_subscription" if str(grants[0]["scope"]).startswith("agent:") else "platform_pool"
    # Carried on the FUNDING response rather than its own endpoint: the proxy already calls this
    # before every uncached model call, so gating on entitlement costs zero extra round trips.
    # A second hot-path call per message would be a real latency tax on every user (DEF-6).
    required, held = _entitlement_state(c, account_id, agent_id)
    if org_id:
        # ORG-ATTRIBUTED TURN (tenancy E2). The pool is the org's; what THIS member may draw of
        # it is bounded by their monthly cap, so the proxy's one existing zero-balance gate
        # enforces both — at/over cap produces the same 402 an empty pool does.
        cap_left = _org_cap_left(c, org_id, account_id)
        capped = cap_left is not None
        if capped:
            remaining = min(remaining, cap_left)
        # An org pool is ALWAYS enforced — "was never on a credit plan" is a personal-tier
        # grace, and extending it to orgs would let an unfunded org run for free forever.
        return {
            "account_id": account_id,
            "org_id": org_id,
            "credits_remaining": remaining,
            "model_tier_max": tier_max,
            "funding_source": "org_pool",
            "credit_class": str(grants[0]["credit_class"]) if grants else "",
            "entitlement_required": required,
            "entitled": held,
            "credits_enforced": True,
            "member_capped": capped and cap_left == 0,
        }
    # HAS THIS ACCOUNT EVER BEEN GRANTED CREDITS? Not "does it have any left" — ANY row, spent or
    # expired. It is what tells the proxy whether a zero balance means "exhausted" (refuse) or
    # "this account was never on a credit plan" (the deployment's free tier — allow, which is
    # what every account did before the gate could fire at all). Without the distinction,
    # switching enforcement on refuses every existing user's very first message.
    ever_granted = c.execute(
        "SELECT 1 FROM credit_grants WHERE account_id = ? AND org_id = '' LIMIT 1", (account_id,)
    ).fetchone() is not None
    return {
        "account_id": account_id,
        "credits_remaining": remaining,
        "model_tier_max": tier_max,
        "funding_source": source,
        "credit_class": str(grants[0]["credit_class"]) if grants else "",
        "entitlement_required": required,
        "entitled": held,
        "credits_enforced": ever_granted,
    }


@app.get("/funding")
def funding(
    account_id: str,
    agent_id: str = "",
    org_id: str = "",
    x_internal_key: str | None = Header(default=None),
) -> dict:
    """What this account can spend right now, and on which model tier. Read by the proxy before
    every uncached call — so it is on the hot path and must stay a single indexed query.
    `org_id` (stamped by the daemon on turns that run an ORG's agent) switches the answer to
    that org's pool, bounded by this member's monthly cap."""
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    with _db() as c:
        if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="unknown account")
        return _funding_view(c, account_id, agent_id, (org_id or "").strip())


@app.get("/me/credits")
def my_credits(
    agent_id: str = "",
    authorization: str | None = Header(default=None),
) -> dict:
    """The signed-in account's OWN balance. The only money endpoint a client may call.

    Everything else here is internal-key only, because a client is user-controlled and must never
    be able to read another account or write its own ledger. This one is safe because it resolves
    the account FROM THE TOKEN rather than taking an account_id — there is no parameter to tamper
    with, so a caller can only ever see themselves.

    It exists because the product sells credits and the app could not show anyone their balance:
    /funding needs the internal key (which a desktop must never hold) and /budget reports dollars
    against a cap, not credits. Without this, "how much do I have left" was answerable only by a
    402 at the moment of failure.
    """
    with _db() as c:
        row = _account_for_token(c, _bearer(authorization))
        account_id = str(row["id"])
        view = _funding_view(c, account_id, agent_id)
        # Soonest expiry, so the UI can warn before an allowance dies rather than after. NULL/0
        # means a grant that never expires, which sorts last.
        soonest = c.execute(
            "SELECT MIN(expires_at) AS e FROM credit_grants WHERE account_id = ? "
            "AND credits > credits_used AND expires_at > ?",
            (account_id, _now()),
        ).fetchone()
    return {
        "email": str(row["email"]),
        "expires_at": float(soonest["e"]) if soonest and soonest["e"] else 0.0,
        **view,
    }


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
    org_id = (payload.get("org_id") or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id required")
    with _db() as c:
        grants = _live_grants(c, account_id, agent_id, org_id)
        available = sum(int(g["credits"]) - int(g["credits_used"]) for g in grants)
        if available <= 0:
            count("debit_total", outcome="insufficient")
            raise HTTPException(
                status_code=402,
                detail=f"insufficient credits: need {credits}, have 0"
                + (f" (org pool {org_id})" if org_id else ""),
            )
        # DRAIN, never refuse, when the balance covers only part of the charge. The call this
        # bills for ALREADY RAN — refusing changes nothing about the money spent, it only leaves
        # the balance untouched, and an untouched balance never reaches zero, so the pre-call
        # gate (which closes at zero) never engages. That was a live incident: a 13-credit
        # account chatted for free indefinitely, every call 402ing here and every 402 leaving
        # the 13 intact. Draining bounds the leak to ONE call's shortfall; the next call finds
        # zero and is refused before the provider is touched. `shortfall` is reported so the
        # caller can meter exactly how much this account overshot.
        drained = min(credits, available)
        shortfall = credits - drained
        left = drained
        for g in grants:
            if left <= 0:
                break
            take = min(left, int(g["credits"]) - int(g["credits_used"]))
            c.execute(
                "UPDATE credit_grants SET credits_used = credits_used + ? WHERE id=?",
                (take, g["id"]),
            )
            left -= take
        view = _funding_view(c, account_id, agent_id, org_id)
    count("debit_total", outcome="drained" if shortfall else "ok")
    # The single number that says "are we selling faster than we are serving?"
    count("credits_consumed_total", drained, _props={"account_id": account_id})
    return {"ok": shortfall == 0, "drained": drained, "shortfall": shortfall, **view}


def _apply_grant(payload: dict) -> dict:
    """Add credits to an account, and post the matching ledger entry. THE MOCKED PURCHASE.

    Real payments are deliberately out of scope (see the plan's NullPaymentProvider seam), but
    the accounting is not: money history cannot be backfilled, so grants, expiry, class and
    consumption are all recorded for real from day one. Swapping a payment rail in later means
    calling this after a successful charge instead of calling it by hand.

    EXTRACTED FROM THE ROUTE so the admin dashboard can grant credits without a second copy of
    this logic. Two callers, two DIFFERENT authorizations (the internal key for infra, an admin
    token for a human), one set of money semantics — which is the half that must never fork.
    """
    account_id = (payload.get("account_id") or "").strip()
    org_id = (payload.get("org_id") or "").strip()
    credits = max(0, int(payload.get("credits") or 0))
    if (not account_id and not org_id) or credits <= 0:
        raise HTTPException(
            status_code=400, detail="an account_id or org_id, and positive credits, required"
        )
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
        if org_id:
            # AN ORG'S POOL (tenancy E2). The row still needs a real account behind it (the FK,
            # and the ledger's idea of whom we owe): the org's primary owner anchors it unless
            # the caller named a buyer. Draw-side only org_id matters — see _live_grants.
            org = c.execute(
                "SELECT id, primary_owner, active FROM orgs WHERE id = ?", (org_id,)
            ).fetchone()
            if org is None or not org["active"]:
                raise HTTPException(status_code=404, detail="unknown organization")
            account_id = account_id or str(org["primary_owner"])
        if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="unknown account")
        ts = _now()
        cur = c.execute(
            "INSERT INTO credit_grants (account_id, org_id, scope, credits, credits_used, "
            "credit_class, model_tier_max, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (account_id, org_id, scope, credits, credit_class, tier_max, expires_at, ts),
        )
        # NO CASH CAME IN, but a liability was still created: we now owe this account service.
        # Posted as a promotional grant whatever the credit_class says, because that is what
        # actually happened -- credits conjured without a payment. /purchase is the only path
        # that books cash, and the only one where a creator accrues anything.
        ledger.post_promotional_grant(
            c, ts, account_id=account_id, credits=credits,
            ref=f"grant:{cur.lastrowid}", idempotency_key=f"grant:{cur.lastrowid}",
        )
        view = _funding_view(c, account_id, "", org_id)
    count("credits_granted_total", credits, credit_class=credit_class, _props={"account_id": account_id})
    return {"ok": True, **view}


@app.post("/grant")
def grant(payload: dict = Body(...), x_internal_key: str | None = Header(default=None)) -> dict:
    """Trusted infra adds credits. The logic is in _apply_grant; this is the internal-key door."""
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    return _apply_grant(payload)


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
    # EXACTLY-ONCE KEY, minted by the proxy when the call completed and carried unchanged through
    # every retry. Absent (an older proxy) falls back to the previous at-least-once behaviour
    # rather than rejecting the row — losing a billing record is worse than duplicating one.
    event_id = str(payload.get("event_id") or "").strip()[:80]
    # WHICH ORG'S POOL paid (tenancy E2) — '' on every personal turn. Forwarded by the proxy
    # from the daemon's per-turn trace; it is what the org rollup and per-member cap read.
    org_id = str(payload.get("org_id") or "").strip()[:64]
    ts = _now()
    with _db() as c:
        if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            count("ledger_row_total", outcome="rejected", reason="unknown_account")
            raise HTTPException(status_code=404, detail="unknown account")
        cur = c.execute(
            "INSERT INTO usage (account_id, ts, month, model, in_tokens, out_tokens, cost_usd, "
            "run_id, turn_id, credits, funding_source, agent_id, model_tier, cached_tokens, "
            "event_id, org_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id) WHERE event_id <> '' DO NOTHING",
            (account_id, ts, _month_key(ts), model, in_tok, out_tok, cost, run_id, turn_id,
             credits, funding_source, agent_id, model_tier, cached_tok, event_id, org_id),
        )
        duplicate = cur.rowcount == 0
        if not duplicate:
            # The double-entry consequence of the same event, in the SAME transaction as the
            # usage row: a provider is owed, and prepaid service was delivered. Keyed by the
            # same event_id so a replay cannot post the money twice either.
            ledger.post_consumption(
                c, ts,
                account_id=account_id,
                cost_micros=ledger.usd_to_micros(cost),
                credits_charged=credits,
                agent_id=agent_id,
                ref=run_id,
                idempotency_key=f"consumption:{event_id}" if event_id else "",
            )
        view = _budget_view(c, account_id)
    if duplicate:
        # Not an error: the proxy did the right thing by retrying. Worth counting because a
        # steady rate means responses are being lost, which is a network or timeout problem.
        count("ledger_row_total", outcome="duplicate", _props={"account_id": account_id, "run_id": run_id})
        return {"ok": True, "duplicate": True, "spent_usd": view["spent_usd"], "over": view["over"],
                "remaining_usd": view["remaining_usd"]}
    # Written from the LEDGER's own side. The proxy counts its attempts; this counts what
    # actually landed. Two independent counters — a gap between them is the interesting signal.
    count("ledger_row_total", outcome="ok", _props={"account_id": account_id, "run_id": run_id})
    if view["over"]:
        count("budget_exceeded_total", _props={"account_id": account_id})
    return {"ok": True, "duplicate": False, "spent_usd": view["spent_usd"], "over": view["over"],
            "remaining_usd": view["remaining_usd"]}


# --- purchases, the ledger, entitlements (plan 2.1-2.5) ----------------------
#
# /grant conjures credits. /purchase is the real path: take money (mocked), create the grant,
# and post the four-way split — liability, reserve, creator accrual, platform margin — in one
# transaction. Everything except the money movement itself is real from day one, because money
# history cannot be backfilled.


@app.post("/products")
def upsert_product(payload: dict = Body(...), x_internal_key: str | None = Header(default=None)) -> dict:
    """Define something sellable. Data, not code: a new price or tier is a row."""
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    pid = (payload.get("id") or "").strip()
    price = float(payload.get("price_usd") or 0)
    if not pid or price <= 0:
        raise HTTPException(status_code=400, detail="id and positive price_usd required")
    # credits omitted => derive from price and the markup, which is the normal case. Stating
    # them explicitly is for a promotional bundle that deliberately breaks the usual ratio.
    _kind = (payload.get("kind") or "credit_pack").strip()
    # Credits derive from price only for credit products — a seat product that names none
    # SELLS none, rather than being handed a derived pool top-up nobody priced.
    credits = int(payload.get("credits") or 0) or (
        ledger.credits_for_usd(price) if _kind != "seat_subscription" else 0
    )
    seats = max(0, int(payload.get("seats") or 0))
    if _kind == "seat_subscription" and seats <= 0:
        raise HTTPException(status_code=400, detail="a seat product needs seats >= 1")
    with _db() as c:
        c.execute(
            "INSERT INTO products (id, kind, title, creator_id, agent_id, price_usd, credits, "
            "scope, model_tier_max, period_days, active, created_at, seats) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, title=excluded.title, "
            "creator_id=excluded.creator_id, agent_id=excluded.agent_id, "
            "price_usd=excluded.price_usd, credits=excluded.credits, scope=excluded.scope, "
            "model_tier_max=excluded.model_tier_max, period_days=excluded.period_days, "
            "active=excluded.active, seats=excluded.seats",
            (
                pid,
                (payload.get("kind") or "credit_pack").strip(),
                (payload.get("title") or "").strip(),
                (payload.get("creator_id") or "").strip(),
                (payload.get("agent_id") or "").strip(),
                price,
                credits,
                (payload.get("scope") or "platform").strip(),
                (payload.get("model_tier_max") or "").strip(),
                int(payload.get("period_days") or 30),
                1 if payload.get("active", True) else 0,
                _now(),
                seats,
            ),
        )
    return {"ok": True, "id": pid, "credits": credits, "seats": seats}


@app.get("/products")
def list_products(kind: str = "") -> dict:
    """Public: the catalogue. No internal key — a marketplace has to be browsable.

    `kind` filters to one shelf (`credit_pack`, `agent_subscription`). The store UI asks for the
    shelf it renders rather than filtering client-side, so adding a third kind of product does
    not make it appear in the credits dialog.
    """
    with _db() as c:
        if kind:
            rows = c.execute(
                "SELECT * FROM products WHERE active = 1 AND kind = ? ORDER BY price_usd",
                (kind.strip(),),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM products WHERE active = 1 ORDER BY price_usd").fetchall()
    # `provider` tells the client which rail is in play, and `payment_note` is the rail's own
    # words for what a purchase will do. The UI DISPLAYS them; it must not branch on them (see
    # payments.py: no code path may work only because payments are mocked).
    rail = build_payment_gateway()
    return {
        "products": [dict(r) for r in rows],
        "provider": rail.name,
        "payment_note": rail.purchase_note,
    }


def _apply_purchase(
    c: sqlite3.Connection,
    *,
    account_id: str,
    price: float,
    credits: int,
    scope: str,
    tier_max: str,
    period_days: int,
    creator_id: str,
    agent_id: str,
    product_id: str,
    idem: str,
    off_session: bool = False,
    org_id: str = "",
    seats: int = 0,
) -> tuple[str, bool, dict, PaymentIntent, float]:
    """Charge, post the books, mint the grant, extend access. Shared by /purchase and renewal.

    Returns `created=False` when the idempotency key had already been posted — the caller MUST
    distinguish that from a fresh charge, or a retried request gets reported as new revenue.

    Extracted because a renewal IS a purchase — same money, same split, same creator accrual —
    and two copies of this sequence would drift, with the divergence showing up as a quiet
    accounting difference between a first purchase and every one after it.

    `off_session=True` says nobody is at a keyboard (a scheduled renewal). The rail needs to know:
    an interactive purchase may send the customer somewhere to authenticate, and a renewal cannot.

    THE ONE-REQUEST SHAPE ONLY WORKS FOR A RAIL THAT SETTLES IMMEDIATELY. A card does not — it
    answers later, on a webhook — so a rail that comes back unsettled is refused here rather than
    reported as bought. That is the 409 below, and it is what `/me/checkout` will exist to avoid.
    """
    ts = _now()
    order = PurchaseOrder(
        account_id=account_id, price_usd=price, credits=credits, scope=scope, tier_max=tier_max,
        period_days=period_days, creator_id=creator_id, agent_id=agent_id, product_id=product_id,
        idempotency_key=idem, org_id=org_id, seats=seats,
    )
    checkout = CheckoutService(
        build_payment_gateway(),
        SqlitePaymentIntentStore(c),
        AccountsPostProcessor(c, ledger, order, at=ts),
        clock=lambda: ts,
    )
    try:
        intent, done = checkout.begin(
            PurchaseRequest(
                account_id=account_id,
                amount=Money.from_usd(price),
                idempotency_key=idem,
                description=product_id or "credit pack",
                meta={"credits": credits, "product_id": product_id},
            ),
            off_session=off_session,
        )
    except ledger.LedgerError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    if done is None:
        if intent.failed:
            raise HTTPException(status_code=402, detail=f"payment failed: {intent.status}")
        raise HTTPException(
            status_code=409,
            detail=f"the {intent.provider} rail cannot settle a purchase in one request "
                   f"(status {intent.status}); this order needs an interactive checkout",
        )
    return done.reference, done.created, done.detail["split"], intent, done.detail["expires_at"]


@app.post("/purchase")
def purchase(payload: dict = Body(...), x_internal_key: str | None = Header(default=None)) -> dict:
    """Buy credits. Charge (mocked) → grant → ledger, in one transaction.

    IDEMPOTENT BY REQUIREMENT, not politeness. A purchase is the one request a user will retry
    when the network hiccups, and charging twice for one intent is the worst bug a payments
    system can have. The caller's key covers the charge, the grant AND the ledger posting, so a
    replay returns the original result and creates nothing.
    """
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    account_id = (payload.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id required")
    idem = str(payload.get("idempotency_key") or "").strip()[:120]
    product_id = (payload.get("product_id") or "").strip()

    with _db() as c:
        if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="unknown account")

        creator_id = agent_id = ""
        scope, tier_max = "platform", ""
        period_days = int(payload.get("expires_days") or 30)
        if product_id:
            p = c.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
            if p is None:
                raise HTTPException(status_code=404, detail=f"unknown product {product_id}")
            price = float(p["price_usd"])
            credits = int(p["credits"]) or ledger.credits_for_usd(price)
            creator_id, agent_id = str(p["creator_id"]), str(p["agent_id"])
            scope = str(p["scope"]) or "platform"
            tier_max = str(p["model_tier_max"] or "")
            period_days = int(p["period_days"] or 30)
        else:
            price = float(payload.get("usd") or 0)
            credits = int(payload.get("credits") or 0) or ledger.credits_for_usd(price)
        if price <= 0 or credits <= 0:
            raise HTTPException(status_code=400, detail="a product_id or a positive usd is required")

        # An already-posted purchase means this is a replay: return the original, charge nothing.
        if idem:
            prior = c.execute(
                "SELECT txn_id, meta FROM ledger_txns WHERE idempotency_key = ?",
                (f"purchase:{idem}",),
            ).fetchone()
            if prior is not None:
                view = _funding_view(c, account_id, agent_id)
                return {"ok": True, "replayed": True, "txn_id": str(prior["txn_id"]),
                        "split": json.loads(prior["meta"] or "{}"), **view}

        txn_id, _created, split, charge, _expires = _apply_purchase(
            c, account_id=account_id, price=price, credits=credits, scope=scope,
            tier_max=tier_max, period_days=period_days, creator_id=creator_id,
            agent_id=agent_id, product_id=product_id, idem=idem,
        )
        view = _funding_view(c, account_id, agent_id)

    count("credits_granted_total", credits, credit_class="paid", _props={"account_id": account_id})
    count("purchase_total", outcome="ok", _props={"account_id": account_id, "product_id": product_id})
    # The three numbers the business is actually made of, separated at the moment of sale.
    count("purchase_gross_usd", price, _props={"account_id": account_id})
    count("reserve_funded_usd", ledger.micros_to_usd(split["reserve_micros"]))
    count("creator_accrued_usd", ledger.micros_to_usd(split["creator_micros"]),
          _props={"creator_id": creator_id})
    return {"ok": True, "replayed": False, "txn_id": txn_id, "credits": credits,
            "charge_reference": charge.reference,
            "split": {k: ledger.micros_to_usd(v) for k, v in split.items()},
            **view}


def _org_purchase_gate(
    c: sqlite3.Connection, account_id: str, org_id: str, kind: str
) -> None:
    """May THIS account make THIS purchase? Raises; returns nothing.

    TWO RULES, both enterprise-shaped:
      * buying FOR an org needs an owner/admin seat in it — a member funding the pool from
        their own card is a support nightmare (whose money was that?), and a stranger doing it
        is worse.
      * an org member has NO personal wallet (see _funding_view), so a personal credit pack
        would buy something their turns can never spend. Refused with the fix in the message
        rather than sold and silently useless.
    """
    memberships = {str(o): str(role) for o, role in orgs_api.org_memberships(c, account_id)}
    if org_id:
        role = memberships.get(org_id)
        if role not in ("owner", "admin"):
            raise HTTPException(
                status_code=403,
                detail="only an owner or admin of this organization may buy for it",
            )
        return
    if kind == "seat_subscription":
        raise HTTPException(
            status_code=400,
            detail="seat products are bought FOR an organization — pass org_id",
        )
    if memberships:
        raise HTTPException(
            status_code=403,
            detail="your organization funds your usage — an org owner or admin can top up "
            "the pool from the Organizations page",
        )


@app.post("/me/purchase")
def my_purchase(
    request: Request,
    payload: dict = Body(...),
    authorization: str | None = Header(default=None),
) -> dict:
    """Buy something as the signed-in account. The second (and last) money endpoint a client may
    call, and the one that lets a user top up from the app instead of asking an operator.

    THE ONLY THING THE CLIENT MAY SEND IS A product_id. Price and credit count are read from the
    `products` row, never from the request — otherwise a user posts `{"usd": 0.01, "credits":
    10000000}` and mints themselves a fortune. `/purchase` still exists for trusted infra and
    still takes an amount, which is exactly why it needs the internal key and this does not.

    IDEMPOTENCY IS NAMESPACED BY ACCOUNT. The client mints the key (one per button press, so a
    double-click or a retried request buys one pack), but a client-chosen key is client-CONTROLLED:
    unnamespaced, account A could send account B's key and be handed B's purchase back as a
    "replay". `me:<account>:<key>` makes that impossible while keeping the retry safety.

    Rate-limited like sign-in: this is a write that costs the platform money to serve.
    """
    _check_rate(request)
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id required")
    # Buying FOR the caller's organization; authorization is _org_purchase_gate's.
    org_id = str(payload.get("org_id") or "").strip()
    client_key = str(payload.get("idempotency_key") or "").strip()[:80]

    with _db() as c:
        row = _account_for_token(c, _bearer(authorization))
        account_id = str(row["id"])
        p = c.execute(
            "SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)
        ).fetchone()
        if p is None:
            count("purchase_total", outcome="failed",
                  _props={"account_id": account_id, "product_id": product_id, "reason": "unknown_product"})
            raise HTTPException(status_code=404, detail=f"unknown product {product_id}")

        price = float(p["price_usd"])
        kind = str(p["kind"] or "credit_pack")
        seats = int(p["seats"] or 0) if "seats" in p.keys() else 0
        # DERIVE CREDITS FROM PRICE ONLY FOR CREDIT PRODUCTS. The fallback exists so a pack row
        # can say "worth whatever $N buys today" — applied to a seat product it would quietly
        # mint a pool top-up nobody priced, on every seat purchase.
        credits = int(p["credits"]) or (
            ledger.credits_for_usd(price) if kind != "seat_subscription" else 0
        )
        idem = f"me:{account_id}:{client_key}" if client_key else ""

        if idem:
            prior = c.execute(
                "SELECT txn_id, meta FROM ledger_txns WHERE idempotency_key = ?",
                (f"purchase:{idem}",),
            ).fetchone()
            if prior is not None:
                view = _funding_view(c, account_id, str(p["agent_id"]))
                return {"ok": True, "replayed": True, "txn_id": str(prior["txn_id"]),
                        "product_id": product_id, "credits": credits, "price_usd": price, **view}

        _org_purchase_gate(c, account_id, org_id, kind)
        txn_id, _created, split, charge, expires_at = _apply_purchase(
            c, account_id=account_id, price=price, credits=credits,
            scope=str(p["scope"]) or "platform", tier_max=str(p["model_tier_max"] or ""),
            period_days=int(p["period_days"] or _credit_pack_days()),
            creator_id=str(p["creator_id"]), agent_id=str(p["agent_id"]),
            product_id=product_id, idem=idem, org_id=org_id, seats=seats,
        )
        view = _funding_view(c, account_id, str(p["agent_id"]))

    # Same counters as /purchase — a self-serve top-up is not a different kind of revenue, and
    # splitting it would make every business number need adding up in two places.
    count("credits_granted_total", credits, credit_class="paid", _props={"account_id": account_id})
    count("purchase_total", outcome="ok",
          _props={"account_id": account_id, "product_id": product_id, "channel": "self_serve"})
    count("purchase_gross_usd", price, _props={"account_id": account_id})
    count("reserve_funded_usd", ledger.micros_to_usd(split["reserve_micros"]))
    if str(p["creator_id"]):
        count("creator_accrued_usd", ledger.micros_to_usd(split["creator_micros"]),
              _props={"creator_id": str(p["creator_id"])})

    return {
        "ok": True, "replayed": False, "txn_id": txn_id, "product_id": product_id,
        "title": str(p["title"] or ""), "credits": credits, "price_usd": price,
        "expires_at": expires_at,
        # What the rail actually did, in the rail's own words. The client shows this; it does not
        # interpret it.
        "payment": {"provider": charge.provider, "status": charge.status,
                    "detail": charge.detail, "reference": charge.reference},
        **view,
    }


def _checkout_return_urls(payload: dict, *, required: bool) -> tuple[str, str]:
    """Where the rail sends the customer back afterwards.

    The client supplies them because only it knows its own origin — a desktop window, the hosted
    web app and an agent's own window are three different places. `AGENTD_CHECKOUT_RETURN_ORIGINS`
    (comma-separated) constrains that to origins we recognise: unset means any absolute http(s)
    URL, which is right for local development and should be set in a deployment, because the
    alternative is an open redirect wearing our domain in the address bar.

    `required` IS THE CONFIGURED RAIL'S ANSWER, NOT A CONVENIENCE. A redirect URL only means
    anything to a rail that redirects. Demanding one from every caller made a rail that settles in
    place — the one this deployment runs on today — refuse checkouts unless the client invented two
    URLs for a journey nobody takes. Every agent window would have carried that lie. Still fully
    validated when it IS supplied, so a caller that sends one on the mock rail is checked anyway.
    """
    env = os.environ.get
    success = str(payload.get("success_url") or env("AGENTD_CHECKOUT_SUCCESS_URL") or "").strip()
    cancel = str(payload.get("cancel_url") or env("AGENTD_CHECKOUT_CANCEL_URL") or "").strip()
    if required and (not success or not cancel):
        raise HTTPException(
            status_code=400,
            detail="success_url and cancel_url are required — the payment page has to know "
                   "where to return the customer",
        )
    configured = (env("AGENTD_CHECKOUT_RETURN_ORIGINS") or "").split(",")
    allowed = [o.strip() for o in configured if o.strip()]
    for url in (success, cancel):
        if not url:
            continue  # legal only when `required` is false; already refused above otherwise
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail=f"{url!r} is not an absolute http(s) URL")
        if allowed and not any(url.startswith(origin) for origin in allowed):
            raise HTTPException(status_code=400, detail="return url is not an allowed origin")
    return success, cancel


#: The page a card rail returns the customer to. DELIBERATELY DUMB: it reads no query params
#: and claims no outcome — only the webhook knows whether money moved, and echoing rail-appended
#: params into a page is how payment ids and tokens end up in browser history. The window the
#: purchase started in learns the real result from the balance (BillingClient.awaitGrant), not
#: from this tab.
_CHECKOUT_COMPLETE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Checkout finished</title>
<style>
  body { margin: 0; display: grid; place-items: center; min-height: 100vh;
         font-family: system-ui, sans-serif; background: #111; color: #eee; }
  main { text-align: center; padding: 2rem; max-width: 26rem; }
  h1 { font-size: 1.3rem; margin: 0 0 .6rem; }
  p { margin: 0; color: #aaa; line-height: 1.5; }
</style></head>
<body><main>
  <h1>Checkout finished</h1>
  <p>You can close this tab and return to the app. Your balance updates automatically once the
  payment is confirmed.</p>
</main>
<script>try { window.close() } catch (e) {}</script>
</body></html>"""


@app.get("/checkout/complete")
def checkout_complete() -> HTMLResponse:
    return HTMLResponse(_CHECKOUT_COMPLETE_HTML)


@app.post("/me/checkout")
def my_checkout(
    request: Request,
    payload: dict = Body(...),
    authorization: str | None = Header(default=None),
) -> dict:
    """Buy something, on a rail that may not finish in this request.

    THE SAME ENDPOINT WORKS ON BOTH RAILS, and the client does not ask which is configured — it
    reads the answer. A rail that settles inline returns the completed purchase (identical to
    `/me/purchase`); a card rail returns `checkout_url`, and the credits arrive when the webhook
    does. A client that follows `checkout_url` when present and shows the balance otherwise is
    correct on either.

    LIKE /me/purchase, THE ONLY THING THE CLIENT MAY SEND IS A product_id (plus where to return
    to). Price and credit count are read from the `products` row — otherwise a user posts their
    own numbers and mints a fortune.
    """
    _check_rate(request)
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id required")
    # Only a rail with a callback sends the customer away and needs somewhere to send
    # them back to. `has_webhook()` is that same question, already answered in one place.
    success_url, cancel_url = _checkout_return_urls(payload, required=has_webhook())
    # Same org semantics as /me/purchase; authorization is _org_purchase_gate's.
    org_id = str(payload.get("org_id") or "").strip()
    client_key = str(payload.get("idempotency_key") or "").strip()[:80]
    ts = _now()

    with _db() as c:
        row = _account_for_token(c, _bearer(authorization))
        account_id = str(row["id"])
        p = c.execute(
            "SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)
        ).fetchone()
        if p is None:
            count("purchase_total", outcome="failed",
                  _props={"account_id": account_id, "product_id": product_id,
                          "reason": "unknown_product"})
            raise HTTPException(status_code=404, detail=f"unknown product {product_id}")

        price = float(p["price_usd"])
        kind = str(p["kind"] or "credit_pack")
        seats = int(p["seats"] or 0) if "seats" in p.keys() else 0
        # DERIVE CREDITS FROM PRICE ONLY FOR CREDIT PRODUCTS. The fallback exists so a pack row
        # can say "worth whatever $N buys today" — applied to a seat product it would quietly
        # mint a pool top-up nobody priced, on every seat purchase.
        credits = int(p["credits"]) or (
            ledger.credits_for_usd(price) if kind != "seat_subscription" else 0
        )
        idem = f"me:{account_id}:{client_key}" if client_key else ""
        _org_purchase_gate(c, account_id, org_id, kind)
        order = PurchaseOrder(
            account_id=account_id, price_usd=price, credits=credits,
            scope=str(p["scope"]) or "platform", tier_max=str(p["model_tier_max"] or ""),
            period_days=int(p["period_days"] or _credit_pack_days()),
            creator_id=str(p["creator_id"]), agent_id=str(p["agent_id"]),
            product_id=product_id, idempotency_key=idem, org_id=org_id,
            seats=seats,
        )

        if idem:
            prior = c.execute(
                "SELECT txn_id FROM ledger_txns WHERE idempotency_key = ?",
                (f"purchase:{idem}",),
            ).fetchone()
            if prior is not None:
                view = _funding_view(c, account_id, order.agent_id)
                return {"ok": True, "replayed": True, "status": "succeeded",
                        "txn_id": str(prior["txn_id"]), "product_id": product_id,
                        "credits": credits, "price_usd": price, **view}

        intent, done = CheckoutService(
            build_payment_gateway(),
            SqlitePaymentIntentStore(c),
            AccountsPostProcessor(c, ledger, order, at=ts),
            clock=lambda: ts,
        ).begin(
            PurchaseRequest(
                account_id=account_id,
                amount=Money.from_usd(price),
                idempotency_key=idem,
                description=str(p["title"] or "") or product_id,
                success_url=success_url,
                cancel_url=cancel_url,
                # The order rides along and comes back signed on the webhook. See
                # PurchaseOrder.to_metadata for why the whole order and not just its id.
                meta=order.to_metadata(),
            )
        )
        view = _funding_view(c, account_id, order.agent_id) if done is not None else {}

    payment = {"provider": intent.provider, "status": intent.status,
               "detail": intent.detail, "reference": intent.reference}

    if done is not None:
        split = done.detail["split"]
        count("credits_granted_total", credits, credit_class="paid",
              _props={"account_id": account_id})
        count("purchase_total", outcome="ok",
              _props={"account_id": account_id, "product_id": product_id, "channel": "checkout"})
        count("purchase_gross_usd", price, _props={"account_id": account_id})
        count("reserve_funded_usd", ledger.micros_to_usd(split["reserve_micros"]))
        if order.creator_id:
            count("creator_accrued_usd", ledger.micros_to_usd(split["creator_micros"]),
                  _props={"creator_id": order.creator_id})
        return {"ok": True, "replayed": False, "status": intent.status,
                "txn_id": done.reference, "product_id": product_id, "title": str(p["title"] or ""),
                "credits": credits, "price_usd": price,
                "expires_at": done.detail["expires_at"], "payment": payment, **view}

    if intent.failed:
        count("purchase_total", outcome="failed",
              _props={"account_id": account_id, "product_id": product_id,
                      "reason": "rail_refused"})
        raise HTTPException(
            status_code=402, detail=intent.detail or f"payment failed: {intent.status}"
        )

    # Started, not finished. NOTHING has been granted and nothing is owed until the callback.
    count("checkout_started_total", _props={"account_id": account_id, "product_id": product_id})
    return {"ok": True, "status": intent.status, "checkout_url": intent.redirect_url,
            "product_id": product_id, "title": str(p["title"] or ""), "credits": credits,
            "price_usd": price, "payment": payment}


@app.post("/subscriptions/renew-due")
def renew_due(x_internal_key: str | None = Header(default=None)) -> dict:
    """Charge and re-grant every subscription whose period has ended (2.2).

    Deliberately a PULLED endpoint rather than a background thread: the accounts service is a
    single container with a SQLite file, and a timer inside it would fire on every replica the
    moment there is more than one — billing every subscriber twice. A scheduler calling this once
    is a thing you can see, retry, and turn off.

    IDEMPOTENT PER PERIOD. The key is the subscription id plus the period it is renewing INTO,
    so calling this twice in the same period charges once, while next period charges again.

    ONE TRANSACTION PER SUBSCRIPTION, NOT ONE PER BATCH (DEF-12). SQLite allows a single writer,
    and `_db()` waits 10s for the lock. Holding one transaction open across the whole batch means
    a renewal run of any size blocks every concurrent /debit and /usage write until it finishes --
    a billing job taking the product down. Committing per subscription holds a short lock many
    times instead of a long lock once. It also makes a mid-batch crash lose only the subscription
    in flight: the ones already charged stay charged, the rest stay due for the next run.
    """
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    now = _now()
    renewed, skipped, failed, already = 0, 0, 0, 0
    # Read the work list, then RELEASE the connection. sqlite3.Row holds plain values, so these
    # rows stay usable after the connection closes.
    with _db() as c:
        due = c.execute(
            "SELECT s.id, s.account_id, s.product_id, s.renews_at, s.org_id AS sub_org, p.* "
            "FROM subscriptions s "
            "JOIN products p ON p.id = s.product_id "
            "WHERE s.status = 'active' AND s.renews_at <> 0 AND s.renews_at <= ? AND p.active = 1",
            (now,),
        ).fetchall()
    for s in due:
        price = float(s["price_usd"])
        sub_org = str(s["sub_org"] or "")
        # Same derivation rule as the storefront: a seat product's credits are whatever its row
        # says and NOTHING when it says nothing — deriving from price would top up the pool by
        # accident on every renewal.
        _kind = str(s["kind"] or "credit_pack")
        credits = int(s["credits"]) or (
            ledger.credits_for_usd(price) if _kind != "seat_subscription" else 0
        )
        # The period being renewed INTO identifies this charge. Reusing the OLD renews_at
        # would re-charge forever once a period is missed; using `now` would let two calls a
        # second apart both charge.
        period_days = int(s["period_days"] or 30)
        if period_days <= 0:
            # A zero or negative period can never move renews_at into the future, so this
            # subscription would be "due" again on the very next run and bill every time.
            # Skip loudly rather than charge in a loop.
            skipped += 1
            count("renewal_total", outcome="skipped", _props={"product_id": str(s["product_id"]), "reason": "bad_period"})
            continue
        idem = f"renew:{int(s['id'])}:{int(float(s['renews_at']))}"
        try:
            with _db() as c:
                _txn, created, _split, _charge, _exp = _apply_purchase(
                    c, account_id=str(s["account_id"]), price=price, credits=credits,
                    scope=str(s["scope"]) or "platform", tier_max=str(s["model_tier_max"] or ""),
                    period_days=period_days, creator_id=str(s["creator_id"]),
                    agent_id=str(s["agent_id"]), product_id=str(s["product_id"]), idem=idem,
                    # seats=0 ON EVERY RENEWAL: a renewal re-charges the seats it already added,
                    # it does not add more. The org still rides along so a pool-funding
                    # subscription keeps granting into the right pocket.
                    org_id=sub_org, seats=0,
                    # Nobody is at a keyboard: this is a scheduler, and a rail that wants the
                    # customer to authenticate has to be told there is no customer to ask.
                    off_session=True,
                )
            # A replay for the same period is NOT a renewal. Counting it as one would report
            # a retried scheduler run as new revenue.
            if created:
                renewed += 1
            else:
                already += 1
        except HTTPException:
            # A declined card must not stop the rest of the batch, and must not cancel the
            # subscription either -- dunning (retry, then notify, then suspend) is its own
            # policy and does not exist yet. Left due, so the next run tries again.
            failed += 1
            count("renewal_total", outcome="failed", _props={"product_id": str(s["product_id"])})
            continue
    if renewed:
        count("renewal_total", renewed, outcome="ok")
    return {"ok": True, "renewed": renewed, "already_charged": already,
            "skipped": skipped, "failed": failed}


@app.post("/ledger/snapshot")
def ledger_snapshot(x_internal_key: str | None = Header(default=None)) -> dict:
    """Publish the balance sheet as metrics (plan 3.3 business metrics).

    WHY A PUSH, AND WHY SCHEDULED. Every other metric here is an EVENT — a call happened, a
    charge succeeded. These are LEVELS: how much reserve is left, how much we owe creators, what
    the cost ratio is. A level has no event to hang off, so nothing would ever emit it; it has to
    be sampled. Calling this on a schedule is what turns the ledger into a graph.
    """
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    with _db() as c:
        b = ledger.balances(c)
        acct = b["accounts"]
        live = c.execute(
            "SELECT COALESCE(SUM(credits - credits_used), 0) AS n FROM credit_grants "
            "WHERE credits > credits_used AND (expires_at = 0 OR expires_at > ?)", (_now(),)
        ).fetchone()

    gauge("reserve_balance_usd", acct["inference_reserve"])
    gauge("creator_payable_usd", acct["creator_payable"])
    gauge("credit_liability_usd", acct["user_credit_liability"])
    gauge("platform_revenue_usd", acct["platform_revenue"])
    gauge("breakage_revenue_usd", acct["breakage_revenue"])
    gauge("provider_cost_usd_total", acct["provider_cost"])
    gauge("gross_margin_usd", b["gross_margin_usd"])
    gauge("credits_outstanding", int(live["n"]))
    # The single number that says whether the business works: share of recognised revenue that
    # went to providers. Above the markup's implied ratio means we are selling below cost.
    revenue = acct["platform_revenue"] + acct["breakage_revenue"]
    gauge("cogs_ratio", round(acct["provider_cost"] / revenue, 4) if revenue > 0 else 0.0)
    # Not a gauge but a health check: must always be 1. Alarmable, unlike a JSON field.
    gauge("ledger_balanced", 1 if b["balanced"] else 0)
    return {"ok": True, **b, "credits_outstanding": int(live["n"])}


@app.get("/health/ready")
def health_ready() -> dict:
    """READINESS, kept separate from /health liveness (plan 3.7).

    /health answers "is the process up" and must never depend on anything downstream — a
    liveness check that fails when a dependency blips gets the whole fleet restarted during
    someone else's outage. This one answers "can it actually do its job": the database must be
    reachable AND WRITABLE, which an EFS mount going read-only would otherwise hide until the
    first user tried to sign up.
    """
    try:
        with _db() as c:
            c.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
            # A read proves the mount exists; only a write proves it still accepts writes.
            c.execute("CREATE TABLE IF NOT EXISTS _readiness (ts REAL)")
            c.execute("DELETE FROM _readiness")
            c.execute("INSERT INTO _readiness (ts) VALUES (?)", (_now(),))
    except Exception as e:  # noqa: BLE001 - any failure at all means not ready
        raise HTTPException(status_code=503, detail=f"not ready: {type(e).__name__}") from e
    return {"ok": True, "db": "writable"}


@app.post("/ledger/close-expired")
def close_expired(x_internal_key: str | None = Header(default=None)) -> dict:
    """Book breakage for grants that expired unspent (2.4).

    Run on a schedule. Until it runs, expired credits are already unspendable (`_live_grants`
    filters them) but the books still carry the liability — so revenue is understated and the
    reserve looks more committed than it is. Idempotent per grant: running it twice is safe.

    One transaction per grant, for the same reason as `renew_due` (DEF-12): a batch-wide
    transaction would hold SQLite's single write lock for the whole run and stall live /debit
    traffic. Idempotency per grant is what makes the smaller transactions safe.
    """
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    now = _now()
    closed, credits_expired = 0, 0
    with _db() as c:
        rows = c.execute(
            "SELECT id, account_id, credits, credits_used FROM credit_grants "
            "WHERE expires_at <> 0 AND expires_at <= ? AND credits > credits_used",
            (now,),
        ).fetchall()
    for r in rows:
        unused = int(r["credits"]) - int(r["credits_used"])
        with _db() as c:
            _txn, created = ledger.post_expiry(
                c, now, account_id=str(r["account_id"]), credits_unused=unused,
                grant_id=int(r["id"]),
            )
        if created:
            closed += 1
            credits_expired += unused
    if credits_expired:
        count("credits_expired_total", credits_expired)
    return {"ok": True, "grants_closed": closed, "credits_expired": credits_expired}


@app.get("/ledger/balances")
def ledger_balances(x_internal_key: str | None = Header(default=None)) -> dict:
    """Every account, plus `balanced` — which must be true. False means a posting bypassed
    `ledger.post()`, and every number here is suspect until that is found."""
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    with _db() as c:
        return ledger.balances(c)


@app.get("/ledger/entries")
def ledger_entries(
    account_id: str = "",
    txn_type: str = "",
    limit: int = 100,
    x_internal_key: str | None = Header(default=None),
) -> dict:
    """Raw entries, newest first. The audit trail: every balance above is re-derivable here."""
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    where, args = [], []
    if account_id:
        where.append("account_id = ?")
        args.append(account_id)
    if txn_type:
        where.append("txn_type = ?")
        args.append(txn_type)
    sql = "SELECT * FROM ledger_entries"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(1000, int(limit))))
    with _db() as c:
        rows = c.execute(sql, args).fetchall()
    return {"entries": [
        {**dict(r), "amount_usd": ledger.micros_to_usd(int(r["amount_micros"]))} for r in rows
    ]}


@app.get("/entitlement")
def entitlement(
    account_id: str,
    agent_id: str,
    x_internal_key: str | None = Header(default=None),
) -> dict:
    """May this account run this agent? Separate from credits: having money is not permission.

    NOT YET ENFORCED at the proxy. Wiring it in means either an extra hot-path call or folding
    the answer into /funding, which the proxy already calls — the latter, when it is done.
    """
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    now = _now()
    with _db() as c:
        row = c.execute(
            "SELECT * FROM entitlements WHERE account_id=? AND agent_id=? "
            "AND (expires_at = 0 OR expires_at > ?)",
            (account_id, agent_id, now),
        ).fetchone()
    return {"account_id": account_id, "agent_id": agent_id, "entitled": row is not None,
            "source": str(row["source"]) if row else "",
            "expires_at": float(row["expires_at"]) if row else 0.0}


@app.post("/entitlement")
def grant_entitlement(payload: dict = Body(...), x_internal_key: str | None = Header(default=None)) -> dict:
    """Grant access without a purchase — a trial, a comp, a creator testing their own agent."""
    if not _require_internal(x_internal_key):
        raise HTTPException(status_code=401, detail="internal key required")
    account_id = (payload.get("account_id") or "").strip()
    agent_id = (payload.get("agent_id") or "").strip()
    if not account_id or not agent_id:
        raise HTTPException(status_code=400, detail="account_id and agent_id required")
    days = float(payload.get("expires_days") or 0)
    ts = _now()
    expires_at = (ts + days * 86_400) if days != 0 else 0.0
    with _db() as c:
        if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="unknown account")
        c.execute(
            "INSERT INTO entitlements (account_id, agent_id, source, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(account_id, agent_id) DO UPDATE SET "
            "source=excluded.source, expires_at=excluded.expires_at",
            (account_id, agent_id, (payload.get("source") or "grant").strip(), expires_at, ts),
        )
    return {"ok": True, "account_id": account_id, "agent_id": agent_id, "expires_at": expires_at}


# --- the rail's callback ------------------------------------------------------


def _handle_payment_event(body: bytes, headers: dict) -> dict:
    """One webhook delivery, inside one transaction.

    THE ORDER IS NOT IN THIS REQUEST. Nobody started it, no session is attached, and the customer
    left minutes or days ago — so the order is rebuilt from the metadata the rail signed back
    (see PurchaseOrder.to_metadata). That is what makes post-processing independent of what the
    products row happens to say now.

    ONE CONNECTION for the dedupe claim, the attempt record and the ledger posting, because they
    must commit together: a claimed event whose grant rolled back is a payment that can never be
    retried and never arrived.
    """
    with _db() as c:
        return PaymentEventService(
            build_webhook_verifier(),
            SqlitePaymentIntentStore(c),
            WebhookPostProcessor(c, ledger, now=_now),
            clock=_now,
        ).handle(body, headers)


# MOUNTED ONLY WHEN A RAIL HAS A CALLBACK. On the mock rail there is nothing to call back, and a
# route that exists only to answer 500 is worse than a 404 — it tells an operator probing the
# service that the webhook is configured when it is not.
if has_webhook():
    app.include_router(build_payment_router(_handle_payment_event))

# /auth/* — the identity module's own surface. Mounted UNCONDITIONALLY, unlike the payment webhook
# above, because the router itself reports 501 when no issuer is configured: "this deployment has
# no platform identity yet" is a real, discoverable answer, whereas a 404 on /auth/login looks
# like a broken build to whoever is debugging a client.
#
# It is handed OUR rate limiter rather than bringing its own, so sign-in has one policy across the
# legacy and token endpoints — two limiters would mean an attacker gets both budgets.
app.include_router(
    build_auth_router(
        _auth_service,
        rate_limit=_check_rate,
        available=identity_factory.tokens_available,
        external_providers=identity_factory.external_providers,
    )
)


# /admin/* — the platform control plane. Mounted unconditionally for the same reason /auth is:
# the router itself answers "you are not an admin" (403) and "this deployment has no admins"
# (also 403, deliberately indistinguishable), both of which are real answers. A 404 would look
# like a broken build to whoever is debugging a dashboard.
#
# THE SETTINGS ARE READ PER CALL, not captured at import. An operator who adds an admin identity
# or points the deployment at a registry restarts one process and it takes effect; a snapshot
# taken at module scope would need a rebuild to notice.
def _rotate_signing_key(c: sqlite3.Connection, retire_after_s: float) -> str:
    """Rotate the token signing key. Lives here rather than in admin_api because building the
    key store is identity's composition concern, and the admin router must not learn it."""
    from identity.infrastructure.sqlite_key_store import SqliteKeyStore

    return SqliteKeyStore(c).rotate(retire_after_s=retire_after_s).kid


# /orgs/* + /me/orgs — enterprise organizations (tenancy plan E1). Mounted unconditionally like
# /auth and /admin: every route resolves the caller's token first and fails closed on
# membership, so on a deployment with no orgs the surface simply answers 404s.
app.include_router(
    orgs_api.build_orgs_router(
        orgs_api.OrgDeps(
            db=_db,
            account_for_token=_account_for_token,
            now=_now,
            month_key=_month_key,
        )
    )
)

app.include_router(
    admin_api.build_admin_router(
        admin_api.AdminDeps(
            db=_db,
            account_for_token=_account_for_token,
            budget_view=_budget_view,
            funding_view=_funding_view,
            apply_grant=_apply_grant,
            revoke_sessions=lambda c, account_id: SqliteRefreshStore(c).revoke_account(account_id),
            rotate_signing_key=_rotate_signing_key,
            ledger_balances=ledger.balances,
            micros_to_usd=ledger.micros_to_usd,
            access_ttl_s=identity_factory.access_ttl_s,
            now=_now,
            month_key=_month_key,
            settings=admin_api.AdminSettings.from_env,
        )
    )
)
