"""The /admin/* surface — the platform control plane, as one router.

WHY THIS IS A SEPARATE MODULE AND NOT MORE OF app.py. Everything in app.py answers a question
about ONE account: the caller's own. These routes answer questions about EVERY account, and they
change other people's money, access and keys. That is a different blast radius and a different
authorization tier, and keeping it behind one door in one file is what makes the door auditable.

THE THIRD AUTHORIZATION TIER. Until this existed a route was reachable by trusted INFRA
(``X-Internal-Key``, a shared service secret) or by the account it was ABOUT (its own bearer
token). A dashboard is neither: it runs in a browser, so it can never hold the internal key, and
it acts on other people's accounts, so owning a token is not enough. The shape here is copied
deliberately from ``RosterAdminService.authorize`` — resolve the token to a real account, then
check that account against a list — so the platform has ONE idea of "admin" rather than two.

WHAT IS DEPLOY DATA, NEVER CODE. Every address, table and secret name this module touches arrives
as configuration (see ``AdminSettings``). Nothing here names a bucket, an ARN, a cluster or a
region. An unset value disables the feature that needs it and says so in the payload, so a
half-configured deployment renders a dashboard with an honest gap in it rather than a stack trace.

DEPENDENCIES ARE INJECTED, for the same reason the auth router takes a service factory: a database
connection lives for one request, and this module must not learn how the host opens one.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

try:
    from agentd_telemetry import count
except ImportError:  # pragma: no cover - telemetry is never load-bearing

    def count(*_a, **_k):  # type: ignore[misc]
        pass


# ============================================================== configuration

#: Page size ceiling. A caller may ask for less; asking for more silently gets this.
PAGE_MAX = 200
PAGE_DEFAULT = 50

#: Floor for how long a retired signing key stays verifiable, and the multiple of the access-token
#: lifetime used above it. DERIVED rather than a fixed number because the constraint is a relation,
#: not a duration: the window MUST outlast any token already in flight, so a deployment that raises
#: AGENTD_AUTH_ACCESS_TTL_S past a hardcoded hour would start signing people out on every rotation.
SIGNING_KEY_RETIRE_FLOOR_S = 3600.0
SIGNING_KEY_RETIRE_TTL_MULTIPLE = 6

#: Timeout for the two outbound calls this module makes (registry, publish service).
OUTBOUND_TIMEOUT_S = 8.0


def _real_secret_id(raw: str) -> str:
    """The Secrets Manager id this deployment writes provider keys to, or "" when there is none.
    "local" is a valid STARTUP source (ambient env) but not a writable secret store, so it maps
    to "" here — the admin keys panel treats it as unconfigured rather than trying to reach it."""
    value = (raw or "").strip()
    return "" if value.lower() == "local" else value


@dataclass(frozen=True)
class AdminSettings:
    """Where everything is. All of it deploy configuration; none of it is defaulted to a real
    resource, because a wrong default here would point the dashboard at another environment."""

    #: Comma-separated emails / account ids that are PERMANENTLY admins. See _config_identities.
    identities: frozenset[str]
    #: Secrets Manager secret holding the platform's provider + platform keys.
    app_secret_id: str
    #: DynamoDB table holding the root key and every creator key, KMS-wrapped.
    creators_table: str
    #: KMS key/alias the creators table is wrapped with (reported, never used to decrypt here).
    kms_key_id: str
    #: ECS cluster whose services must roll when a secret changes.
    ecs_cluster: str
    #: {secret key -> [service names]} — who reads what, so a write knows what to restart.
    key_consumers: dict[str, list[str]]
    #: The signed registry index (agents, versions, roster).
    registry_url: str
    #: The publish service, for creator admission.
    publish_url: str
    #: AWS region for the boto3 clients.
    region: str

    @classmethod
    def from_env(cls) -> AdminSettings:
        raw_identities = os.environ.get("AGENTD_ADMIN_IDENTITIES", "") or ""
        try:
            consumers = json.loads(os.environ.get("AGENTD_KEY_CONSUMERS", "") or "{}")
            if not isinstance(consumers, dict):
                raise ValueError("not an object")
            consumers = {
                str(k): [str(s) for s in (v if isinstance(v, list) else [v])]
                for k, v in consumers.items()
            }
        except ValueError:
            # A typo in a rollout map must not take the dashboard down; it degrades to "we do not
            # know who reads this key", which the write path reports rather than guessing.
            consumers = {}
        return cls(
            identities=frozenset(
                p.strip().lower() for p in raw_identities.split(",") if p.strip()
            ),
            # "local" is a real startup source (the ambient environment) but NOT a Secrets Manager
            # secret this admin panel can read or write to — so it reads as unconfigured HERE,
            # which makes the keys panel show an honest gap and refuse writes (503) instead of
            # trying to reach a secret named "local" and 502-ing. Only a real id is configured.
            app_secret_id=_real_secret_id(os.environ.get("AGENTD_APP_SECRET_ID", "")),
            creators_table=(os.environ.get("AGENTD_CREATORS_TABLE", "") or "").strip(),
            kms_key_id=(os.environ.get("AGENTD_PUBLISH_KMS_KEY", "") or "").strip(),
            ecs_cluster=(os.environ.get("AGENTD_ECS_CLUSTER", "") or "").strip(),
            key_consumers=consumers,
            registry_url=(os.environ.get("AGENTD_REGISTRY", "") or "").strip(),
            publish_url=(os.environ.get("AGENTD_PUBLISH_URL", "") or "").strip().rstrip("/"),
            region=(os.environ.get("AWS_REGION", "") or "").strip(),
        )


@dataclass(frozen=True)
class AdminDeps:
    """The host's own primitives. Injected so this module never opens a connection itself."""

    db: Callable[[], AbstractContextManager[sqlite3.Connection]]
    account_for_token: Callable[[sqlite3.Connection, str], sqlite3.Row]
    budget_view: Callable[[sqlite3.Connection, str], dict]
    funding_view: Callable[[sqlite3.Connection, str, str], dict]
    apply_grant: Callable[[dict], dict]
    revoke_sessions: Callable[[sqlite3.Connection, str], int]
    rotate_signing_key: Callable[[sqlite3.Connection, float], str]
    #: ledger.balances — the chart of accounts plus the "do the books balance" check.
    ledger_balances: Callable[[sqlite3.Connection], dict]
    #: ledger.micros_to_usd — entries store positive micros and carry the sign in `direction`.
    micros_to_usd: Callable[[int], float]
    access_ttl_s: Callable[[], int]
    now: Callable[[], float]
    month_key: Callable[[float], str]
    settings: Callable[[], AdminSettings]


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization[len("Bearer ") :].strip()


def _page(limit: Any, offset: Any) -> tuple[int, int]:
    """Clamp, never trust.

    THIS IS NOT DEFENSIVE PADDING. Accounts is single-writer SQLite on a network filesystem at
    desired_count=1, and these routes share a process with /resolve and /funding — the two calls
    in front of every model call the platform serves. A dashboard that asks for 100000 rows would
    add latency to every user's every message, so the ceiling is enforced here rather than trusted
    to the client that happens to be shipping today.
    """
    try:
        size = int(limit or PAGE_DEFAULT)
    except (TypeError, ValueError):
        size = PAGE_DEFAULT
    try:
        start = int(offset or 0)
    except (TypeError, ValueError):
        start = 0
    return max(1, min(size, PAGE_MAX)), max(0, start)


# ============================================================== authorization


def _identity_set(account_id: str, email: str) -> set[str]:
    """The strings an admin list may name someone by. Same pair RosterAdminService accepts, so one
    identity works against both services."""
    return {str(account_id or "").strip().lower(), str(email or "").strip().lower()} - {""}


def is_admin(c: sqlite3.Connection, account_id: str, email: str, cfg: AdminSettings) -> bool:
    """Either source grants it: deploy config, or the editable roster table."""
    if _identity_set(account_id, email) & cfg.identities:
        return True
    row = c.execute(
        "SELECT 1 FROM admins WHERE account_id = ? AND active = 1", (account_id,)
    ).fetchone()
    return row is not None


def admin_source(c: sqlite3.Connection, account_id: str, email: str, cfg: AdminSettings) -> str:
    """'config' (permanent), 'roster' (editable), or '' (not an admin).

    Rendered by the dashboard so nobody wastes a click trying to demote a break-glass admin and
    then wonders why the list did not change.
    """
    if _identity_set(account_id, email) & cfg.identities:
        return "config"
    row = c.execute(
        "SELECT 1 FROM admins WHERE account_id = ? AND active = 1", (account_id,)
    ).fetchone()
    return "roster" if row is not None else ""


# ============================================================== AWS, lazily


def _boto(service: str, cfg: AdminSettings):
    """A boto3 client, or None when the SDK or the region is absent.

    NONE IS A SUPPORTED ANSWER, not a failure. This service runs on a laptop during development
    with no AWS credentials at all, and the correct behaviour there is a dashboard whose key panel
    says "not configured" — not one that 500s, and not one that silently pretends the keys are
    fine.
    """
    try:
        import boto3  # noqa: PLC0415 - deliberately deferred; see docstring
    except ImportError:
        return None
    try:
        return boto3.client(service, region_name=cfg.region or None)
    except Exception:  # noqa: BLE001 - a missing profile must not take the route down
        return None


def _fetch_json(url: str) -> dict:
    """GET a JSON document. Returns {} on any failure — callers report the gap in the payload."""
    if not url:
        return {}
    try:
        with urllib.request.urlopen(url, timeout=OUTBOUND_TIMEOUT_S) as r:
            return json.loads(r.read() or b"{}")
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return {}


def _publish_base(cfg: AdminSettings) -> str:
    """The publish service's address, or a 503 that names the gap.

    Checked on the BASE rather than on the joined url: an unset base concatenates into a
    perfectly plausible-looking relative path ("/registry/admin/creators"), which urllib then
    fails to open with an error about the URL type — a confusing way to say "this deployment has
    no publish service configured".
    """
    if not cfg.publish_url:
        raise HTTPException(
            status_code=503, detail="no publish service is configured for this deployment"
        )
    return cfg.publish_url


def _proxy(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    """Call another service AS THE ADMIN, forwarding their token.

    WHY PROXY RATHER THAN LET THE BROWSER CALL IT. The publish service is a Lambda behind the same
    load balancer on a different port, so a browser calling it directly is a cross-origin request
    that would need CORS on every response there. Going through this service instead gives the
    dashboard ONE origin and keeps publish's own authorization exactly where it is — it still sees
    the admin's token and still decides for itself.
    """
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=OUTBOUND_TIMEOUT_S) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        # The downstream's refusal is the answer, passed through verbatim. Rewriting a 403 into a
        # 502 would tell an admin the service is broken when it is working correctly.
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"message": e.reason or "the publish service refused"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HTTPException(
            status_code=502, detail=f"the publish service is unreachable: {e}"
        ) from e


# ============================================================== the router


def build_admin_router(deps: AdminDeps) -> APIRouter:  # noqa: PLR0915 - one cohesive surface
    router = APIRouter(prefix="/admin", tags=["admin"])

    def require_admin(authorization: str | None) -> sqlite3.Row:
        """The door. Returns the calling admin's account row, or raises.

        Two failures, told apart on purpose: 401 means "we do not know who you are" and is fixed
        by signing in; 403 means "we know, and no" and is not. Beyond that the 403 says nothing
        about who IS an admin — that is not information a non-admin is owed.
        """
        token = _bearer(authorization)
        cfg = deps.settings()
        with deps.db() as c:
            row = deps.account_for_token(c, token)
            if not is_admin(c, row["id"], row["email"], cfg):
                count("admin_call_total", outcome="forbidden", _props={"account_id": row["id"]})
                raise HTTPException(
                    status_code=403, detail="this account is not a platform admin"
                )
        return row

    # ---------------------------------------------------------------- whoami

    @router.get("/whoami")
    def whoami(authorization: str | None = Header(default=None)) -> dict:
        """Is the caller an admin? The client asks ONCE on load and renders its nav from this.

        Deliberately NOT folded into /resolve, which runs before every uncached model call — the
        platform's hot path. An extra table read there would tax every user's every message to
        answer a question the UI asks once per session.

        Answers 200 for everyone rather than 403 for non-admins: the client needs "no" as DATA to
        decide whether to render the entry, and an error would be indistinguishable from the
        service being down, which would hide the nav from real admins during an incident. It is
        also what the publish service calls to decide the same question, so there is one authority
        for "is X an admin" in the platform rather than one list per service.
        """
        token = _bearer(authorization)
        cfg = deps.settings()
        with deps.db() as c:
            row = deps.account_for_token(c, token)
            source = admin_source(c, row["id"], row["email"], cfg)
        return {
            "account_id": row["id"],
            "email": row["email"],
            "is_admin": bool(source),
            "source": source,
        }

    # ---------------------------------------------------------------- overview

    @router.get("/overview")
    def overview(authorization: str | None = Header(default=None)) -> dict:
        """The numbers a morning check wants, in one call.

        Every aggregate is scoped to the CURRENT MONTH and therefore rides ix_usage_acct_month
        rather than scanning the ledger. The ledger only grows; a dashboard that gets slower every
        month is one that eventually takes the platform's hot path down with it.
        """
        require_admin(authorization)
        with deps.db() as c:
            month = deps.month_key(deps.now())
            accounts_total = int(c.execute("SELECT COUNT(*) n FROM accounts").fetchone()["n"])
            accounts_active = int(
                c.execute("SELECT COUNT(*) n FROM accounts WHERE active = 1").fetchone()["n"]
            )
            spend = c.execute(
                "SELECT COUNT(*) calls, SUM(cost_usd) cost, SUM(in_tokens) tin, "
                "SUM(out_tokens) tout, SUM(cached_tokens) tcached, SUM(credits) credits "
                "FROM usage WHERE month = ?",
                (month,),
            ).fetchone()
            outstanding = c.execute(
                "SELECT SUM(credits - credits_used) n FROM credit_grants "
                "WHERE expires_at = 0 OR expires_at > ?",
                (deps.now(),),
            ).fetchone()
            top_agents = [
                {
                    "agent_id": r["agent_id"] or "(unattributed)",
                    "calls": int(r["calls"] or 0),
                    "cost_usd": round(float(r["cost"] or 0.0), 6),
                    "in_tokens": int(r["tin"] or 0),
                    "out_tokens": int(r["tout"] or 0),
                }
                for r in c.execute(
                    "SELECT agent_id, COUNT(*) calls, SUM(cost_usd) cost, SUM(in_tokens) tin, "
                    "SUM(out_tokens) tout FROM usage WHERE month = ? "
                    "GROUP BY agent_id ORDER BY cost DESC LIMIT 10",
                    (month,),
                )
            ]
            top_accounts = [
                {
                    "account_id": r["account_id"],
                    "email": r["email"] or "",
                    "calls": int(r["calls"] or 0),
                    "cost_usd": round(float(r["cost"] or 0.0), 6),
                }
                for r in c.execute(
                    "SELECT u.account_id, a.email, COUNT(*) calls, SUM(u.cost_usd) cost "
                    "FROM usage u LEFT JOIN accounts a ON a.id = u.account_id "
                    "WHERE u.month = ? GROUP BY u.account_id ORDER BY cost DESC LIMIT 10",
                    (month,),
                )
            ]
            admins_count = int(
                c.execute("SELECT COUNT(*) n FROM admins WHERE active = 1").fetchone()["n"]
            )
        return {
            "month": month,
            "accounts_total": accounts_total,
            "accounts_active": accounts_active,
            "admins": admins_count,
            "calls": int(spend["calls"] or 0),
            "cost_usd": round(float(spend["cost"] or 0.0), 6),
            "in_tokens": int(spend["tin"] or 0),
            "out_tokens": int(spend["tout"] or 0),
            "cached_tokens": int(spend["tcached"] or 0),
            "credits_spent": int(spend["credits"] or 0),
            "credits_outstanding": int(outstanding["n"] or 0),
            "top_agents": top_agents,
            "top_accounts": top_accounts,
        }

    # ---------------------------------------------------------------- accounts

    def _sums_for(c: sqlite3.Connection, ids: list[str]) -> tuple[dict, dict]:
        """Month spend and live balance for a PAGE of accounts, in two queries.

        Two for the whole page rather than two per row: the per-row shape turns a 50-row listing
        into 100 statements against a database whose other reader is the platform's hot path.
        """
        if not ids:
            return {}, {}
        marks = ",".join("?" * len(ids))
        spent = {
            str(r["account_id"]): float(r["total"] or 0.0)
            for r in c.execute(
                f"SELECT account_id, SUM(cost_usd) total FROM usage "  # noqa: S608 - marks are '?'
                f"WHERE month = ? AND account_id IN ({marks}) GROUP BY account_id",
                (deps.month_key(deps.now()), *ids),
            )
        }
        credits = {
            str(r["account_id"]): int(r["total"] or 0)
            for r in c.execute(
                f"SELECT account_id, SUM(credits - credits_used) total FROM credit_grants "  # noqa: S608
                f"WHERE account_id IN ({marks}) AND (expires_at = 0 OR expires_at > ?) "
                f"GROUP BY account_id",
                (*ids, deps.now()),
            )
        }
        return spent, credits

    @router.get("/accounts")
    def list_accounts(
        q: str = "",
        limit: int = PAGE_DEFAULT,
        offset: int = 0,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Every account, newest first. `q` matches email or id as a substring."""
        require_admin(authorization)
        cfg = deps.settings()
        size, start = _page(limit, offset)
        needle = f"%{q.strip().lower()}%" if q and q.strip() else ""
        with deps.db() as c:
            if needle:
                where, args = "WHERE lower(email) LIKE ? OR lower(id) LIKE ?", (needle, needle)
            else:
                where, args = "", ()
            total = int(
                c.execute(f"SELECT COUNT(*) n FROM accounts {where}", args).fetchone()[  # noqa: S608
                    "n"
                ]
            )
            rows = c.execute(
                f"SELECT id, email, budget_usd, active, created_at FROM accounts {where} "  # noqa: S608
                f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*args, size, start),
            ).fetchall()
            ids = [str(r["id"]) for r in rows]
            spent, credits = _sums_for(c, ids)
            roster = {
                str(r["account_id"])
                for r in c.execute("SELECT account_id FROM admins WHERE active = 1")
            }
            accounts = [
                {
                    "account_id": r["id"],
                    "email": r["email"],
                    "created_at": r["created_at"],
                    "active": bool(r["active"]),
                    "budget_usd": r["budget_usd"],
                    "spent_usd": round(spent.get(str(r["id"]), 0.0), 6),
                    "credits_remaining": credits.get(str(r["id"]), 0),
                    "admin_source": (
                        "config"
                        if _identity_set(str(r["id"]), str(r["email"])) & cfg.identities
                        else ("roster" if str(r["id"]) in roster else "")
                    ),
                }
                for r in rows
            ]
        return {"accounts": accounts, "total": total, "limit": size, "offset": start}

    @router.get("/accounts/{account_id}")
    def account_detail(
        account_id: str, authorization: str | None = Header(default=None)
    ) -> dict:
        """One account: everything this service knows about it."""
        require_admin(authorization)
        cfg = deps.settings()
        with deps.db() as c:
            row = c.execute(
                "SELECT id, email, budget_usd, active, created_at FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown account")
            funding = deps.funding_view(c, account_id, "")
            budget = deps.budget_view(c, account_id)
            month = deps.month_key(deps.now())
            grants = [
                dict(g)
                for g in c.execute(
                    "SELECT id, scope, credits, credits_used, credit_class, model_tier_max, "
                    "expires_at, created_at FROM credit_grants WHERE account_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (account_id, PAGE_DEFAULT),
                )
            ]
            recent = [
                dict(u)
                for u in c.execute(
                    "SELECT ts, model, agent_id, in_tokens, out_tokens, cached_tokens, "
                    "cost_usd, credits, funding_source FROM usage WHERE account_id = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (account_id, PAGE_DEFAULT),
                )
            ]
            by_agent = [
                {
                    "agent_id": r["agent_id"] or "(unattributed)",
                    "calls": int(r["calls"] or 0),
                    "in_tokens": int(r["tin"] or 0),
                    "out_tokens": int(r["tout"] or 0),
                    "cost_usd": round(float(r["cost"] or 0.0), 6),
                }
                for r in c.execute(
                    "SELECT agent_id, COUNT(*) calls, SUM(in_tokens) tin, SUM(out_tokens) tout, "
                    "SUM(cost_usd) cost FROM usage WHERE account_id = ? AND month = ? "
                    "GROUP BY agent_id ORDER BY cost DESC",
                    (account_id, month),
                )
            ]
            entitlements = [
                dict(e)
                for e in c.execute(
                    "SELECT agent_id, source, min_version, expires_at, created_at "
                    "FROM entitlements WHERE account_id = ? ORDER BY created_at DESC",
                    (account_id,),
                )
            ]
            subs = [
                dict(s)
                for s in c.execute(
                    "SELECT product_id, status, renews_at, created_at FROM subscriptions "
                    "WHERE account_id = ? ORDER BY created_at DESC",
                    (account_id,),
                )
            ]
            # The refresh table IS the login history: `used_at` is the last time this device
            # exchanged its token, which is the closest thing to "last seen" we hold.
            devices = [
                {
                    "family_id": d["family_id"],
                    "client_id": d["client_id"],
                    "device_label": d["device_label"],
                    "issued_at": d["issued_at"],
                    "used_at": d["used_at"],
                    "expires_at": d["expires_at"],
                    "revoked": bool(d["revoked_at"]),
                }
                for d in c.execute(
                    "SELECT family_id, client_id, device_label, issued_at, used_at, "
                    "expires_at, revoked_at FROM refresh_tokens WHERE account_id = ? "
                    "ORDER BY issued_at DESC LIMIT ?",
                    (account_id, PAGE_DEFAULT),
                )
            ]
            source = admin_source(c, str(row["id"]), str(row["email"]), cfg)
        return {
            "account_id": row["id"],
            "email": row["email"],
            "created_at": row["created_at"],
            "active": bool(row["active"]),
            "budget_usd": row["budget_usd"],
            "spent_usd": budget["spent_usd"],
            "over": budget["over"],
            "credits_remaining": funding.get("credits_remaining", 0),
            "credits_enforced": funding.get("credits_enforced", False),
            "is_admin": bool(source),
            "admin_source": source,
            "grants": grants,
            "recent_usage": recent,
            "usage_by_agent": by_agent,
            "entitlements": entitlements,
            "subscriptions": subs,
            "devices": devices,
        }

    @router.post("/accounts/{account_id}/budget")
    def set_budget(
        account_id: str,
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Set or clear the monthly dollar cap. `budget_usd: null` = unlimited.

        NULL genuinely means unlimited (see _budget_view: `over` can only be true when a budget is
        set), so clearing this removes a limit rather than resetting it to some default.
        """
        require_admin(authorization)
        raw = payload.get("budget_usd")
        if raw in (None, ""):
            value = None
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError) as e:
                raise HTTPException(
                    status_code=400, detail="budget_usd must be a number or null"
                ) from e
            if value < 0:
                raise HTTPException(status_code=400, detail="budget_usd cannot be negative")
        with deps.db() as c:
            if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="unknown account")
            c.execute("UPDATE accounts SET budget_usd = ? WHERE id = ?", (value, account_id))
            view = deps.budget_view(c, account_id)
        count("admin_action_total", outcome="budget", _props={"account_id": account_id})
        return {"ok": True, **view}

    @router.post("/accounts/{account_id}/active")
    def set_active(
        account_id: str,
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Enable or disable an account.

        Takes effect IMMEDIATELY despite live tokens: _account_for_token re-reads the accounts row
        on every call and refuses an inactive one, so a disabled account cannot ride out the
        remaining minutes of an already-issued access token.
        """
        admin = require_admin(authorization)
        active = bool(payload.get("active"))
        if account_id == admin["id"] and not active:
            # The one self-inflicted lockout worth refusing outright: re-enabling requires signing
            # in, and signing in requires the account to be enabled.
            raise HTTPException(status_code=400, detail="you cannot disable your own account")
        with deps.db() as c:
            if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="unknown account")
            c.execute(
                "UPDATE accounts SET active = ? WHERE id = ?", (1 if active else 0, account_id)
            )
        count(
            "admin_action_total",
            outcome="enabled" if active else "disabled",
            _props={"account_id": account_id},
        )
        return {"ok": True, "account_id": account_id, "active": active}

    @router.post("/accounts/{account_id}/credits")
    def grant_credits(
        account_id: str,
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Grant credits. Same money path as /grant, different door.

        NOTE THE SIDE EFFECT, because it is the one that surprises: an account's FIRST grant flips
        `credits_enforced` on permanently (_funding_view reads "has this account ever been
        granted"). From then on a zero balance refuses calls instead of falling through to the free
        tier. Granting is therefore also the act of putting someone on a metered plan.
        """
        require_admin(authorization)
        result = deps.apply_grant({**payload, "account_id": account_id})
        count("admin_action_total", outcome="granted", _props={"account_id": account_id})
        return result

    @router.post("/accounts/{account_id}/sessions/revoke")
    def revoke_sessions(
        account_id: str, authorization: str | None = Header(default=None)
    ) -> dict:
        """Sign this account out of every device.

        Kills refresh tokens, ending the ability to obtain NEW access tokens. Tokens already
        issued stay valid until they expire — up to `access_ttl_s`. Reported rather than hidden:
        an admin revoking a compromised session needs to know the window is minutes and to pair
        this with disabling the account if it must be instant.
        """
        require_admin(authorization)
        with deps.db() as c:
            if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="unknown account")
            revoked = deps.revoke_sessions(c, account_id)
        count("admin_action_total", outcome="sessions_revoked", _props={"account_id": account_id})
        return {
            "ok": True,
            "revoked": revoked,
            "access_tokens_valid_for_s": deps.access_ttl_s(),
        }

    @router.post("/accounts/{account_id}/admin")
    def set_admin(
        account_id: str,
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Promote or demote a platform admin.

        Refuses to demote a CONFIG admin, and says why: that identity comes from deploy
        configuration which this table cannot override, so a silent no-op would leave the dashboard
        showing a change that did not happen. Also refuses self-demotion — the same lockout
        reasoning as disabling your own account, and the reason the config list exists at all.
        """
        admin = require_admin(authorization)
        cfg = deps.settings()
        make_admin = bool(payload.get("is_admin"))
        if account_id == admin["id"] and not make_admin:
            raise HTTPException(
                status_code=400, detail="you cannot remove your own admin access"
            )
        with deps.db() as c:
            row = c.execute(
                "SELECT id, email FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown account")
            source = admin_source(c, str(row["id"]), str(row["email"]), cfg)
            if source == "config" and not make_admin:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "this account is an admin through deploy configuration "
                        "(AGENTD_ADMIN_IDENTITIES) and cannot be demoted here — remove it from "
                        "publish_admin_identities and apply"
                    ),
                )
            if make_admin:
                c.execute(
                    "INSERT INTO admins (account_id, email, added_by, added_at, active) "
                    "VALUES (?, ?, ?, ?, 1) ON CONFLICT(account_id) DO UPDATE SET "
                    "active = 1, added_by = excluded.added_by, added_at = excluded.added_at",
                    (account_id, row["email"], admin["id"], deps.now()),
                )
            else:
                c.execute("UPDATE admins SET active = 0 WHERE account_id = ?", (account_id,))
        count(
            "admin_action_total",
            outcome="promoted" if make_admin else "demoted",
            _props={"account_id": account_id},
        )
        return {"ok": True, "account_id": account_id, "is_admin": make_admin}

    # ---------------------------------------------------------------- usage

    @router.get("/usage")
    def usage(
        month: str = "",
        agent_id: str = "",
        account_id: str = "",
        group_by: str = "agent",
        limit: int = PAGE_DEFAULT,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Token and cost rollups. `group_by` is agent | model | account | day.

        The grouping column is chosen from a FIXED MAP rather than interpolated from the query
        string. It is the only place in this module where a caller's value could reach SQL as
        anything but a bound parameter, and a column name cannot be bound — so the safe form is a
        lookup that can only ever produce one of four known strings.
        """
        require_admin(authorization)
        size, _ = _page(limit, 0)
        columns = {
            "agent": "agent_id",
            "model": "model",
            "account": "account_id",
            "day": "date(ts, 'unixepoch')",
        }
        column = columns.get((group_by or "agent").strip().lower())
        if column is None:
            raise HTTPException(
                status_code=400, detail=f"group_by must be one of {sorted(columns)}"
            )
        period = (month or "").strip() or deps.month_key(deps.now())
        where = ["month = ?"]
        args: list[Any] = [period]
        if agent_id.strip():
            where.append("agent_id = ?")
            args.append(agent_id.strip())
        if account_id.strip():
            where.append("account_id = ?")
            args.append(account_id.strip())
        clause = " AND ".join(where)
        with deps.db() as c:
            rows = [
                {
                    "key": str(r["k"] or "(unattributed)"),
                    "calls": int(r["calls"] or 0),
                    "in_tokens": int(r["tin"] or 0),
                    "out_tokens": int(r["tout"] or 0),
                    "cached_tokens": int(r["tcached"] or 0),
                    "credits": int(r["credits"] or 0),
                    "cost_usd": round(float(r["cost"] or 0.0), 6),
                }
                for r in c.execute(
                    f"SELECT {column} k, COUNT(*) calls, SUM(in_tokens) tin, "  # noqa: S608 - fixed map
                    f"SUM(out_tokens) tout, SUM(cached_tokens) tcached, SUM(credits) credits, "
                    f"SUM(cost_usd) cost FROM usage WHERE {clause} "
                    f"GROUP BY k ORDER BY cost DESC LIMIT ?",
                    (*args, size),
                )
            ]
            months = [
                str(r["month"])
                for r in c.execute(
                    "SELECT DISTINCT month FROM usage ORDER BY month DESC LIMIT 24"
                )
            ]
        return {"month": period, "group_by": group_by, "rows": rows, "months": months}

    # ---------------------------------------------------------------- money

    @router.get("/products")
    def list_products(authorization: str | None = Header(default=None)) -> dict:
        """Everything for sale — credit packs and agent subscriptions."""
        require_admin(authorization)
        with deps.db() as c:
            products = [
                dict(p)
                for p in c.execute(
                    "SELECT id, kind, title, creator_id, agent_id, price_usd, credits, scope, "
                    "model_tier_max, period_days, active, created_at FROM products "
                    "ORDER BY kind, price_usd"
                )
            ]
            sold = {
                str(r["product_id"]): int(r["n"] or 0)
                for r in c.execute(
                    "SELECT product_id, COUNT(*) n FROM subscriptions GROUP BY product_id"
                )
            }
        for p in products:
            p["active"] = bool(p["active"])
            p["subscribers"] = sold.get(str(p["id"]), 0)
        return {"products": products}

    @router.post("/products")
    def upsert_product(
        payload: dict = Body(...), authorization: str | None = Header(default=None)
    ) -> dict:
        """Create or update a product. Price and availability are DATA, never a deploy.

        An UPSERT rather than separate create/update routes because the id is caller-chosen and
        stable: editing a price must keep the same product so existing subscriptions still point
        at something.
        """
        require_admin(authorization)
        product_id = str(payload.get("id") or "").strip()
        if not product_id:
            raise HTTPException(status_code=400, detail="id required")
        kind = str(payload.get("kind") or "credit_pack").strip()
        if kind not in ("credit_pack", "agent_subscription"):
            raise HTTPException(
                status_code=400, detail="kind must be credit_pack or agent_subscription"
            )
        try:
            price = float(payload.get("price_usd") or 0)
            credits = int(payload.get("credits") or 0)
            period = int(payload.get("period_days") or 30)
        except (TypeError, ValueError) as e:
            raise HTTPException(
                status_code=400, detail="price_usd, credits and period_days must be numbers"
            ) from e
        if price < 0 or credits < 0:
            raise HTTPException(status_code=400, detail="price and credits cannot be negative")
        row = (
            product_id,
            kind,
            str(payload.get("title") or "").strip(),
            str(payload.get("creator_id") or "").strip(),
            str(payload.get("agent_id") or "").strip(),
            price,
            credits,
            str(payload.get("scope") or "platform").strip() or "platform",
            str(payload.get("model_tier_max") or "").strip(),
            period,
            1 if payload.get("active", True) else 0,
            deps.now(),
        )
        with deps.db() as c:
            c.execute(
                "INSERT INTO products (id, kind, title, creator_id, agent_id, price_usd, "
                "credits, scope, model_tier_max, period_days, active, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "kind=excluded.kind, title=excluded.title, creator_id=excluded.creator_id, "
                "agent_id=excluded.agent_id, price_usd=excluded.price_usd, "
                "credits=excluded.credits, scope=excluded.scope, "
                "model_tier_max=excluded.model_tier_max, period_days=excluded.period_days, "
                "active=excluded.active",
                row,
            )
        count("admin_action_total", outcome="product", _props={"product_id": product_id})
        return {"ok": True, "id": product_id}

    @router.post("/accounts/{account_id}/entitlements")
    def set_entitlement(
        account_id: str,
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Grant or remove access to one agent, independent of money.

        Separate from credits deliberately: having a balance is not the same as being allowed.
        """
        require_admin(authorization)
        agent_id = str(payload.get("agent_id") or "").strip()
        if not agent_id:
            raise HTTPException(status_code=400, detail="agent_id required")
        granted = bool(payload.get("granted", True))
        days = float(payload.get("expires_days") or 0)
        expires_at = (deps.now() + days * 86_400) if days != 0 else 0.0
        with deps.db() as c:
            if c.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="unknown account")
            if granted:
                c.execute(
                    "INSERT INTO entitlements (account_id, agent_id, source, min_version, "
                    "expires_at, created_at) VALUES (?, ?, 'grant', ?, ?, ?) "
                    "ON CONFLICT(account_id, agent_id) DO UPDATE SET source='grant', "
                    "min_version=excluded.min_version, expires_at=excluded.expires_at",
                    (
                        account_id,
                        agent_id,
                        str(payload.get("min_version") or "").strip(),
                        expires_at,
                        deps.now(),
                    ),
                )
            else:
                c.execute(
                    "DELETE FROM entitlements WHERE account_id = ? AND agent_id = ?",
                    (account_id, agent_id),
                )
        count("admin_action_total", outcome="entitlement", _props={"account_id": account_id})
        return {"ok": True, "account_id": account_id, "agent_id": agent_id, "granted": granted}

    @router.get("/ledger")
    def ledger_view(
        limit: int = PAGE_DEFAULT,
        offset: int = 0,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Double-entry balances plus the most recent postings.

        Reads the ledger's own tables directly rather than through the internal-key routes: those
        exist for trusted infra and take a shared service secret a browser must never hold.
        """
        require_admin(authorization)
        size, start = _page(limit, offset)
        with deps.db() as c:
            # ledger.balances() rather than a SUM here: it normalises the sign per account type
            # (assets grow on the debit side, revenue on the credit side) and returns the residual
            # check. Re-deriving that in a dashboard query is how the two would drift.
            books = deps.ledger_balances(c)
            entries = [
                {
                    "id": e["id"],
                    "ts": e["ts"],
                    "txn_id": e["txn_id"],
                    "txn_type": e["txn_type"],
                    "account": e["account"],
                    "direction": e["direction"],
                    "amount_usd": deps.micros_to_usd(int(e["amount_micros"] or 0)),
                    "account_id": e["account_id"],
                    "agent_id": e["agent_id"],
                    "ref": e["ref"],
                }
                for e in c.execute(
                    "SELECT id, ts, txn_id, txn_type, account, direction, amount_micros, "
                    "account_id, agent_id, ref FROM ledger_entries "
                    "ORDER BY id DESC LIMIT ? OFFSET ?",
                    (size, start),
                )
            ]
            total = int(c.execute("SELECT COUNT(*) n FROM ledger_entries").fetchone()["n"])
        return {
            "accounts": books.get("accounts", {}),
            # Must be true. False means a posting bypassed ledger.post(), which is a correctness
            # bug in the books rather than a display problem — so it is surfaced, not hidden.
            "balanced": books.get("balanced", True),
            "residual_usd": books.get("residual_usd", 0.0),
            "gross_margin_usd": books.get("gross_margin_usd", 0.0),
            "entries": entries,
            "total": total,
            "limit": size,
            "offset": start,
        }

    # ---------------------------------------------------------------- orgs

    @router.get("/orgs")
    def orgs(
        authorization: str | None = Header(default=None),
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        """Every organization: seats, members, pool remaining, month spend — the platform
        operator's view (tenancy E1). Org ADMINS see their own org through /orgs/{id}; this
        panel is for the person who runs the deployment, behind the same admin door as
        everything else here."""
        require_admin(authorization)
        size, start = _page(limit, offset)
        with deps.db() as c:
            month = deps.month_key(deps.now())
            total = int(c.execute("SELECT COUNT(*) n FROM orgs").fetchone()["n"])
            rows = c.execute(
                "SELECT o.id, o.name, o.primary_owner, o.seats_total, o.active, o.created_at, "
                "a.email AS owner_email, "
                "(SELECT COUNT(*) FROM org_members m WHERE m.org_id = o.id AND m.active = 1) "
                "  AS seats_used, "
                "(SELECT COALESCE(SUM(g.credits - g.credits_used), 0) FROM credit_grants g "
                "  WHERE g.org_id = o.id AND g.credits > g.credits_used "
                "  AND (g.expires_at = 0 OR g.expires_at > ?)) AS pool_remaining, "
                "(SELECT COALESCE(SUM(u.credits), 0) FROM usage u "
                "  WHERE u.org_id = o.id AND u.month = ?) AS month_credits "
                "FROM orgs o LEFT JOIN accounts a ON a.id = o.primary_owner "
                "ORDER BY o.created_at DESC LIMIT ? OFFSET ?",
                (deps.now(), month, size, start),
            ).fetchall()
        return {
            "orgs": [
                {
                    "id": str(r["id"]),
                    "name": str(r["name"]),
                    "primary_owner": str(r["primary_owner"]),
                    "owner_email": str(r["owner_email"] or ""),
                    "seats_total": int(r["seats_total"] or 0),
                    "seats_used": int(r["seats_used"] or 0),
                    "active": bool(r["active"]),
                    "created_at": float(r["created_at"] or 0),
                    "pool_credits_remaining": int(r["pool_remaining"] or 0),
                    "month_credits": int(r["month_credits"] or 0),
                }
                for r in rows
            ],
            "total": total,
            "limit": size,
            "offset": start,
        }

    @router.post("/orgs/{org_id}/credits")
    def org_credits(
        org_id: str,
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Grant credits to an ORG'S POOL — the same _apply_grant money semantics the personal
        grant action uses (ledger posting included), with the org as the target."""
        admin = require_admin(authorization)
        result = deps.apply_grant({**dict(payload or {}), "org_id": org_id})
        count(
            "admin_action_total", action="org_grant",
            _props={"account_id": admin["id"], "org_id": org_id},
        )
        return result

    @router.post("/orgs/{org_id}/active")
    def org_active(
        org_id: str,
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Suspend / reinstate an org. Suspension vanishes it from every member's NEXT token
        (one access-TTL), stops org-pool funding answers, and 404s its routes — membership
        rows are kept, so reinstating restores everyone."""
        admin = require_admin(authorization)
        active = 1 if payload.get("active") else 0
        with deps.db() as c:
            row = c.execute("SELECT 1 FROM orgs WHERE id = ?", (org_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown organization")
            c.execute("UPDATE orgs SET active = ? WHERE id = ?", (active, org_id))
        count(
            "admin_action_total", action="org_active",
            _props={"account_id": admin["id"], "org_id": org_id, "active": active},
        )
        return {"ok": True, "org_id": org_id, "active": bool(active)}

    # ---------------------------------------------------------------- catalog

    @router.get("/agents")
    def agents(authorization: str | None = Header(default=None)) -> dict:
        """Published agents, their versions and who signed them — read from the signed index.

        Fetched SERVER-SIDE rather than from the browser so the dashboard has one origin, and so
        an admin sees exactly the document a client would verify rather than a CDN-cached view of
        it.
        """
        require_admin(authorization)
        cfg = deps.settings()
        index = _fetch_json(cfg.registry_url)
        if not index:
            return {
                "configured": bool(cfg.registry_url),
                "registry_url": cfg.registry_url,
                "bundles": [],
                "engine": {},
                "roster": [],
                "error": "the registry index could not be read",
            }
        publishers = index.get("publishers") or {}
        roster = publishers.get("roster") or []
        names = {str(r.get("id")): str(r.get("name") or r.get("id")) for r in roster}
        revoked = {str(r.get("id")) for r in (publishers.get("revoked") or [])}
        bundles = [
            {
                "id": b.get("id"),
                "version": b.get("version"),
                "title": b.get("title") or b.get("id"),
                "description": b.get("description") or "",
                "publisher_id": b.get("publisher_id") or "",
                "publisher_name": names.get(str(b.get("publisher_id") or ""), ""),
                "publisher_revoked": str(b.get("publisher_id") or "") in revoked,
                "delivery": b.get("delivery") or {},
                "installers": b.get("installers") or [],
                "size": b.get("size") or 0,
            }
            for b in (index.get("bundles") or [])
        ]
        # ONE ROW PER PLATFORM, always a list. The index carries `engine` as a list today and
        # carried a bare object in an earlier schema; normalising here means the client renders one
        # shape and a registry written by either version displays correctly. Reading it as an
        # object when it is a list does not crash — it silently shows nothing, which is the worst
        # of the three outcomes and is exactly what this avoids.
        raw_engine = index.get("engine") or []
        engines = [raw_engine] if isinstance(raw_engine, dict) else list(raw_engine)
        return {
            "configured": True,
            "registry_url": cfg.registry_url,
            "schema": index.get("schema"),
            "bundles": bundles,
            "engines": engines,
            "web": index.get("web") or {},
            "roster": roster,
            "revoked": sorted(revoked),
        }

    @router.get("/creators")
    def creators(authorization: str | None = Header(default=None)) -> dict:
        """Every creator and their state, from the publish service.

        Proxied rather than called from the browser: publish is a Lambda on another port, so a
        direct call would be cross-origin. Going through here keeps ONE origin for the dashboard
        and leaves publish's own authorization exactly where it is — it still sees the admin's
        token and still decides for itself.
        """
        admin_token = _bearer(authorization)
        require_admin(authorization)
        cfg = deps.settings()
        base = _publish_base(cfg)
        status, body = _proxy("GET", f"{base}/registry/admin/creators", admin_token)
        if status == 404:
            # AN OLDER PUBLISH IMAGE has `pending` but not the full listing. Degrade to what it
            # does have — but SAY SO, because the degraded answer is indistinguishable from the
            # healthy one in the worst possible case: a registry whose creators are all already
            # admitted has an empty `pending`, so a silent fallback renders "no creators" on a
            # marketplace that has several. That is a wrong answer presented as a confident one.
            status, body = _proxy("GET", f"{base}/registry/admin/pending", admin_token)
            if status < 400:
                waiting = body.get("pending") or []
                for row in waiting:
                    # `pending` omits the state field because everything it returns has the same
                    # one. Stamped here so the client renders one shape either way.
                    row.setdefault("state", "pending_review")
                return {
                    "creators": waiting,
                    "partial": True,
                    "reason": (
                        "This publish service predates the full creator listing, so only creators "
                        "still AWAITING REVIEW are shown — anyone already admitted or revoked is "
                        "missing. Deploy the current publish image to see everyone."
                    ),
                }
        if status >= 400:
            raise HTTPException(status_code=status, detail=body.get("message") or "refused")
        return body

    @router.post("/creators/admit")
    def admit_creator(
        payload: dict = Body(...), authorization: str | None = Header(default=None)
    ) -> dict:
        """Admit one creator or everyone waiting. Re-signs the roster with the platform root key,
        marks them listed, and publishes whatever they parked — in that order, which is the only
        one with no window where a client would reject a bundle we just signed."""
        admin_token = _bearer(authorization)
        require_admin(authorization)
        cfg = deps.settings()
        status, body = _proxy(
            "POST", f"{_publish_base(cfg)}/registry/admin/admit", admin_token, payload
        )
        if status >= 400:
            raise HTTPException(status_code=status, detail=body.get("message") or "refused")
        count("admin_action_total", outcome="creator_admitted")
        return body

    @router.post("/agents/unlist")
    def unlist_agent(
        payload: dict = Body(...), authorization: str | None = Header(default=None)
    ) -> dict:
        """Take an agent off the marketplace.

        Removes the LISTING, not the files and not anyone's installed copy — so republishing the
        same version restores it. Deleting the artifacts is deliberately still a CLI operation
        (`agentd bundle unlist --purge-artifacts`): it is the one step here with no way back, and a
        button is the wrong shape for it.
        """
        admin_token = _bearer(authorization)
        require_admin(authorization)
        cfg = deps.settings()
        status, body = _proxy(
            "POST", f"{_publish_base(cfg)}/registry/admin/unlist", admin_token, payload
        )
        if status >= 400:
            raise HTTPException(status_code=status, detail=body.get("message") or "refused")
        count("admin_action_total", outcome="agent_unlisted")
        return body

    @router.post("/creators/revoke")
    def revoke_creator(
        payload: dict = Body(...), authorization: str | None = Header(default=None)
    ) -> dict:
        """Revoke a creator. Every client refuses everything they signed on its next index fetch.
        This stops NEW installs and updates; it is not a remote uninstall of existing copies."""
        admin_token = _bearer(authorization)
        require_admin(authorization)
        cfg = deps.settings()
        status, body = _proxy(
            "POST", f"{_publish_base(cfg)}/registry/admin/revoke", admin_token, payload
        )
        if status >= 400:
            raise HTTPException(status_code=status, detail=body.get("message") or "refused")
        count("admin_action_total", outcome="creator_revoked")
        return body

    # ---------------------------------------------------------------- keys

    @router.get("/keys")
    def keys(authorization: str | None = Header(default=None)) -> dict:
        """Every key the platform holds: what it is, where it lives, and whether it is healthy.

        NO SECRET VALUE IS EVER RETURNED, from any of the four sources. A browser page that can
        display a provider key is a browser page that can leak one, and the operational question
        an admin actually has — is it set, how old is it, is it wrapped — is answered entirely by
        metadata. Sources that are not configured report themselves as such rather than being
        omitted, so a missing panel is never mistaken for a missing key.
        """
        require_admin(authorization)
        cfg = deps.settings()
        with deps.db() as c:
            signing = [
                {
                    "kid": r["kid"],
                    "alg": r["alg"],
                    "active": bool(r["active"]),
                    "encrypted": bool(r["encrypted"]),
                    "created_at": r["created_at"],
                    "expires_at": r["expires_at"],
                }
                for r in c.execute(
                    "SELECT kid, alg, active, encrypted, created_at, expires_at "
                    "FROM signing_keys ORDER BY active DESC, created_at DESC"
                )
            ]

        # --- Secrets Manager: names and rotation dates, never values.
        secrets_view: dict = {"configured": bool(cfg.app_secret_id), "id": cfg.app_secret_id}
        if cfg.app_secret_id:
            client = _boto("secretsmanager", cfg)
            if client is None:
                secrets_view["error"] = "no AWS client available in this process"
            else:
                try:
                    meta = client.describe_secret(SecretId=cfg.app_secret_id)
                    # The VALUE is fetched only to learn which keys exist and whether any is still
                    # the placeholder. Key NAMES and a set/unset flag leave this process; the
                    # values do not, and are not logged.
                    raw = client.get_secret_value(SecretId=cfg.app_secret_id)
                    try:
                        parsed = json.loads(raw.get("SecretString") or "{}")
                    except ValueError:
                        parsed = {}
                    secrets_view["last_changed"] = str(meta.get("LastChangedDate") or "")
                    secrets_view["last_rotated"] = str(meta.get("LastRotatedDate") or "")
                    secrets_view["keys"] = [
                        {
                            "name": name,
                            "set": bool(value) and value != "REPLACE_ME",
                            "placeholder": value == "REPLACE_ME",
                            "consumers": cfg.key_consumers.get(name, []),
                        }
                        for name, value in sorted(parsed.items())
                    ]
                except Exception as e:  # noqa: BLE001 - report, never crash the panel
                    secrets_view["error"] = str(e)[:200]

        # --- DynamoDB: the root key and every creator key, wrapped.
        creators_view: dict = {
            "configured": bool(cfg.creators_table),
            "table": cfg.creators_table,
            "kms_key": cfg.kms_key_id,
        }
        if cfg.creators_table:
            client = _boto("dynamodb", cfg)
            if client is None:
                creators_view["error"] = "no AWS client available in this process"
            else:
                try:
                    scanned = client.scan(
                        TableName=cfg.creators_table,
                        ProjectionExpression="creator_id,#n,#s,public_key,created,admitted,"
                        "private_key",
                        ExpressionAttributeNames={"#n": "name", "#s": "state"},
                    )
                    creators_view["keys"] = [
                        {
                            "creator_id": item.get("creator_id", {}).get("S", ""),
                            "name": item.get("name", {}).get("S", ""),
                            "state": item.get("state", {}).get("S", ""),
                            "public_key": item.get("public_key", {}).get("S", ""),
                            "created": item.get("created", {}).get("S", ""),
                            "admitted": item.get("admitted", {}).get("S", ""),
                            # The wrapped blob's PRESENCE is the health signal; its contents are
                            # neither read nor returned.
                            "wrapped": bool(item.get("private_key", {}).get("S", "")),
                        }
                        for item in scanned.get("Items", [])
                    ]
                except Exception as e:  # noqa: BLE001
                    creators_view["error"] = str(e)[:200]

        return {
            "signing_keys": signing,
            "signing_key_kek": bool(os.environ.get("AGENTD_IDENTITY_KEK", "")),
            "secrets": secrets_view,
            "creator_keys": creators_view,
        }

    @router.post("/keys/signing/rotate")
    def rotate_signing(authorization: str | None = Header(default=None)) -> dict:
        """Mint a new token signing key and retire the current one.

        NOBODY IS SIGNED OUT. The outgoing key stays verifiable while the new one signs, so tokens
        issued a moment ago keep working and JWKS serves both until the old one lapses. That
        overlap is the whole reason rotation is safe to do during the working day — and it is sized
        from this deployment's own access-token lifetime, so it cannot be made too short by
        lengthening tokens somewhere else.
        """
        require_admin(authorization)
        retire_after = max(
            SIGNING_KEY_RETIRE_FLOOR_S,
            float(deps.access_ttl_s() or 0) * SIGNING_KEY_RETIRE_TTL_MULTIPLE,
        )
        with deps.db() as c:
            kid = deps.rotate_signing_key(c, retire_after)
        count("admin_action_total", outcome="signing_key_rotated")
        return {"ok": True, "kid": kid, "previous_key_valid_for_s": retire_after}

    @router.post("/keys/secret")
    def set_secret(
        payload: dict = Body(...), authorization: str | None = Header(default=None)
    ) -> dict:
        """Set one key inside the app secret, then roll the services that read it.

        THE ROLL IS PART OF THE ACTION, NOT A FOLLOW-UP. ECS injects secrets at container start,
        so writing a new provider key and stopping there leaves every service running the old
        value with no error anywhere — the change appears to have worked and has not. Which
        services read which key is configuration (AGENTD_KEY_CONSUMERS), so adding a key later
        needs no code change here.

        READ-MODIFY-WRITE of the whole document, because Secrets Manager stores one string: the
        current value is fetched, ONE field is replaced, and the rest is written back untouched.
        Every other key keeps its value, and none of them is logged or returned.
        """
        require_admin(authorization)
        cfg = deps.settings()
        name = str(payload.get("name") or "").strip()
        value = payload.get("value")
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        if not isinstance(value, str) or not value:
            raise HTTPException(status_code=400, detail="a non-empty string value is required")
        if not cfg.app_secret_id:
            raise HTTPException(
                status_code=503, detail="no app secret is configured for this deployment"
            )
        client = _boto("secretsmanager", cfg)
        if client is None:
            raise HTTPException(
                status_code=503, detail="this process has no AWS client available"
            )
        try:
            current = client.get_secret_value(SecretId=cfg.app_secret_id)
            document = json.loads(current.get("SecretString") or "{}")
            if not isinstance(document, dict):
                raise ValueError("the app secret is not a JSON object")
            document[name] = value
            client.put_secret_value(
                SecretId=cfg.app_secret_id, SecretString=json.dumps(document)
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"could not write the secret: {e}") from e

        rolled, roll_errors = [], []
        consumers = cfg.key_consumers.get(name, [])
        ecs = _boto("ecs", cfg) if consumers and cfg.ecs_cluster else None
        if consumers and ecs is not None:
            for service in consumers:
                try:
                    ecs.update_service(
                        cluster=cfg.ecs_cluster, service=service, forceNewDeployment=True
                    )
                    rolled.append(service)
                except Exception as e:  # noqa: BLE001
                    roll_errors.append(f"{service}: {str(e)[:120]}")
        count("admin_action_total", outcome="secret_set", _props={"secret": name})
        return {
            "ok": True,
            "name": name,
            "rolled": rolled,
            "roll_errors": roll_errors,
            # Said plainly rather than implied: if nothing rolled, the new value is stored and NOT
            # yet in use by anything.
            "in_effect": bool(rolled) and not roll_errors,
            "note": (
                "the new value is stored but takes effect only when the services that read it "
                "restart"
                if not rolled
                else "rolling deployment started; the new value is live once tasks are healthy"
            ),
        }

    return router
