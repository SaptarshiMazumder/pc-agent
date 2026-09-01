"""The /orgs/* surface — enterprise organizations, as one router (tenancy plan E1).

AN ORG IS NOT A NEW KIND OF TENANT — it is a tenant that accounts are MEMBERS of. Nothing in
this module touches how a personal account works: an account with no membership rows takes no
new branch anywhere, which is what keeps desktop and single-user hosted behaviour byte-identical.

THE ONE RULE EVERY ROUTE FOLLOWS: the org id is NEVER trusted from the client without a live
membership row behind it, and a missing row always fails CLOSED (403/404), never open. That is
the `SET LOCAL` lesson from the ChatGPT/Redis incident applied to HTTP — org context is proven
per call, never ambient.

Tables live in identity's versioned ledger (identity/infrastructure/sqlite_schema.py, step 2)
because membership is a fact about WHO someone is; this module owns the routes and the
membership queries. Composed by app.py exactly the way admin_api is: dependencies injected, no
connection opened here, loaded as a sibling module in both the image and the by-path test run.

DOMAINS ARE FREE TEXT FOR NOW — an org admin types any domain string and matching emails are
OFFERED the join at sign-in (never silently added). No DNS check, no member-email guardrail:
explicit user decision (2026-08-18); the Notion guardrail and DNS verification are the hardening
step before real enterprises.
"""

from __future__ import annotations

import os
import hashlib
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from fastapi import APIRouter, Body, Header, HTTPException

try:
    from agentd_telemetry import count
except ImportError:  # pragma: no cover - telemetry is never load-bearing

    def count(*_a, **_k):  # type: ignore[misc]
        pass


ROLES = ("owner", "admin", "member")
#: Roles that may administer an org (invites, members, domains, usage).
ADMIN_ROLES = ("owner", "admin")
#: Default invite lifetime. Long enough to survive a weekend, short enough that a link pasted
#: into a wiki two months ago is dead.
INVITE_TTL_DAYS_DEFAULT = 7.0
INVITE_TTL_DAYS_MAX = 30.0
#: New orgs start small on purpose: seats are the membership gate, and an unbounded default
#: would make the gate decorative until an admin remembered to set it.
#: Seats an org is born with, BEFORE any are bought. Env-driven so dev stays friendly while
#: production prices honestly: AGENTD_ORG_FREE_SEATS=1 there means the owner's own seat is free
#: and every other one is a seat_subscription purchase.
SEATS_DEFAULT = max(1, int(os.environ.get("AGENTD_ORG_FREE_SEATS", "5")))
SEATS_MAX = 10_000

#: Free/consumer email providers whose domain must NEVER be claimed by an org — otherwise every
#: person with a @gmail.com address would route into whichever org claimed it first. BOTH the
#: create path (which infers the domain from the owner's email) and the manual add path skip
#: these. A SEED only: AGENTD_PUBLIC_EMAIL_DOMAINS (comma/space separated) overrides it, so the
#: policy is config-owned, not baked in — an operator extends or replaces the list without a code
#: change. Kept short and obvious; the long tail is the operator's to add.
_PUBLIC_EMAIL_DOMAINS_SEED = (
    "gmail.com googlemail.com outlook.com hotmail.com live.com msn.com yahoo.com ymail.com "
    "icloud.com me.com mac.com aol.com proton.me protonmail.com gmx.com mail.com yandex.com "
    "zoho.com pm.me hey.com fastmail.com"
)


def public_email_domains() -> frozenset[str]:
    """Provider domains an org may not claim — the env override wins over the seed entirely."""
    raw = os.environ.get("AGENTD_PUBLIC_EMAIL_DOMAINS", "").strip() or _PUBLIC_EMAIL_DOMAINS_SEED
    return frozenset(d.strip().lower() for d in raw.replace(",", " ").split() if d.strip())


def is_public_email_domain(domain: str) -> bool:
    return (domain or "").strip().lower() in public_email_domains()


def email_domain(email: str) -> str:
    """The lowercased domain part of an email, or '' if it has none / looks malformed."""
    dom = (email or "").rsplit("@", 1)[-1].strip().lower()
    return "" if (not dom or "@" in dom or "." not in dom) else dom


@dataclass(frozen=True)
class OrgDeps:
    """The host's primitives, injected — same shape and same reason as AdminDeps."""

    db: Callable[[], AbstractContextManager[sqlite3.Connection]]
    account_for_token: Callable[[sqlite3.Connection, str], sqlite3.Row]
    now: Callable[[], float]
    month_key: Callable[[float], str]


# ============================================================== shared queries
# These are the ONE implementation of "which orgs is this account in" — the token issuer's
# org_resolver, the login offer, and every route below all call these, so the answer cannot fork.


def org_memberships(c: sqlite3.Connection, account_id: str) -> tuple[tuple[str, str], ...]:
    """Active memberships in active orgs -> ((org_id, role), ...), the token's `orgs` claim.

    BOTH active flags matter: a suspended org must vanish from every member's next token, and a
    deactivated member must vanish from the org — each within one access-token TTL.
    """
    rows = c.execute(
        "SELECT m.org_id, m.role FROM org_members m JOIN orgs o ON o.id = m.org_id "
        "WHERE m.account_id = ? AND m.active = 1 AND o.active = 1 ORDER BY m.org_id",
        (account_id,),
    ).fetchall()
    return tuple((str(r["org_id"]), str(r["role"])) for r in rows)


def joinable_orgs(c: sqlite3.Connection, email: str, account_id: str) -> list[dict]:
    """Orgs whose allowed domains match this email — minus the ones already joined.

    The OFFER, never the membership: silent auto-add is how a contractor lands inside the wrong
    wall, so the client renders these and the person chooses (Notion's rule).
    """
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not domain or "@" in domain:
        return []
    rows = c.execute(
        "SELECT o.id, o.name FROM org_domains d JOIN orgs o ON o.id = d.org_id "
        "WHERE d.domain = ? AND o.active = 1 "
        "AND NOT EXISTS (SELECT 1 FROM org_members m "
        "                WHERE m.org_id = o.id AND m.account_id = ? AND m.active = 1) "
        "ORDER BY o.name",
        (domain, account_id),
    ).fetchall()
    return [{"id": str(r["id"]), "name": str(r["name"])} for r in rows]


def _org_row(c: sqlite3.Connection, org_id: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone()
    if row is None or not row["active"]:
        # One answer for "no such org" and "suspended org": a caller who is not inside is not
        # owed the difference.
        raise HTTPException(status_code=404, detail="unknown organization")
    return row


def _member_role(c: sqlite3.Connection, org_id: str, account_id: str) -> str:
    """This account's active role in the org, or '' — the fail-closed membership check."""
    row = c.execute(
        "SELECT role FROM org_members WHERE org_id = ? AND account_id = ? AND active = 1",
        (org_id, account_id),
    ).fetchone()
    return str(row["role"]) if row else ""


def _require_member(c: sqlite3.Connection, org_id: str, account_id: str) -> str:
    role = _member_role(c, org_id, account_id)
    if not role:
        # 404, not 403: whether the org EXISTS is itself information a non-member is not owed
        # (the same rule the admin door applies to who its admins are).
        raise HTTPException(status_code=404, detail="unknown organization")
    return role


def _require_org_admin(c: sqlite3.Connection, org_id: str, account_id: str) -> str:
    role = _require_member(c, org_id, account_id)
    if role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="requires an org admin or owner")
    return role


def _seats_used(c: sqlite3.Connection, org_id: str) -> int:
    row = c.execute(
        "SELECT COUNT(*) AS n FROM org_members WHERE org_id = ? AND active = 1", (org_id,)
    ).fetchone()
    return int(row["n"] or 0)


def _seat_available(c: sqlite3.Connection, org: sqlite3.Row) -> None:
    """Seats gate MEMBERSHIP, never model calls — one gate per question (plan E2)."""
    if _seats_used(c, str(org["id"])) >= int(org["seats_total"] or 0):
        raise HTTPException(
            status_code=409,
            detail=f"no seats left ({int(org['seats_total'])} of {int(org['seats_total'])} in use)",
        )


def _add_member(
    c: sqlite3.Connection,
    org: sqlite3.Row,
    account_id: str,
    *,
    role: str,
    added_by: str,
    at: float,
) -> None:
    """Insert-or-reactivate, behind the seats gate. One writer for every join path (create,
    invite, domain), so the seat check cannot be forgotten by the next path added."""
    existing = c.execute(
        "SELECT active FROM org_members WHERE org_id = ? AND account_id = ?",
        (str(org["id"]), account_id),
    ).fetchone()
    if existing is not None and existing["active"]:
        raise HTTPException(status_code=409, detail="already a member")
    # ONE ORG PER ACCOUNT. Not a simplification — the funding rule depends on it: a member's
    # every turn draws THEIR org's pool, and with two orgs there is no honest answer to which.
    # Enforced at the one writer every join path uses, so no new path can forget it.
    elsewhere = c.execute(
        "SELECT o.name FROM org_members m JOIN orgs o ON o.id = m.org_id "
        "WHERE m.account_id = ? AND m.active = 1 AND o.active = 1 AND m.org_id <> ? LIMIT 1",
        (account_id, str(org["id"])),
    ).fetchone()
    if elsewhere is not None:
        raise HTTPException(
            status_code=409,
            detail=f"this account already belongs to '{elsewhere['name']}' — an account can be "
            f"in one organization; leave it first",
        )
    _seat_available(c, org)
    c.execute(
        "INSERT INTO org_members (org_id, account_id, role, monthly_credit_cap, added_by, "
        "added_at, active) VALUES (?, ?, ?, 0, ?, ?, 1) "
        "ON CONFLICT(org_id, account_id) DO UPDATE SET "
        "role = excluded.role, added_by = excluded.added_by, added_at = excluded.added_at, "
        "active = 1",
        (str(org["id"]), account_id, role, added_by, at),
    )


def _member_view(r: sqlite3.Row) -> dict:
    return {
        "account_id": str(r["account_id"]),
        "email": str(r["email"] or ""),
        "role": str(r["role"]),
        "monthly_credit_cap": int(r["monthly_credit_cap"] or 0),
        "added_at": float(r["added_at"] or 0),
    }


def _org_view(c: sqlite3.Connection, org: sqlite3.Row, role: str) -> dict:
    org_id = str(org["id"])
    out = {
        "id": org_id,
        "name": str(org["name"]),
        "seats_total": int(org["seats_total"] or 0),
        "seats_used": _seats_used(c, org_id),
        "role": role,
        "created_at": float(org["created_at"] or 0),
    }
    if role in ADMIN_ROLES:
        # Members, domains and the credit pool are admin-view data. A plain member sees the
        # org exists and their own role — enough for the switcher, nothing about colleagues.
        members = c.execute(
            "SELECT m.account_id, m.role, m.monthly_credit_cap, m.added_at, a.email "
            "FROM org_members m LEFT JOIN accounts a ON a.id = m.account_id "
            "WHERE m.org_id = ? AND m.active = 1 ORDER BY m.added_at, m.account_id",
            (org_id,),
        ).fetchall()
        out["members"] = [_member_view(r) for r in members]
        out["primary_owner"] = str(org["primary_owner"])
        domains = c.execute(
            "SELECT domain FROM org_domains WHERE org_id = ? ORDER BY domain", (org_id,)
        ).fetchall()
        out["domains"] = [str(r["domain"]) for r in domains]
        pool = c.execute(
            "SELECT COALESCE(SUM(credits - credits_used), 0) AS left FROM credit_grants "
            "WHERE org_id = ? AND credits > credits_used AND (expires_at = 0 OR expires_at > ?)",
            (org_id, time.time()),
        ).fetchone()
        out["pool_credits_remaining"] = int(pool["left"] or 0)
    return out


def _hash_invite(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_domain(raw: str) -> str:
    d = str(raw or "").strip().lower().lstrip("@")
    if not d or "@" in d or "/" in d or " " in d or "." not in d:
        raise HTTPException(status_code=400, detail="that does not look like a domain")
    return d


# ============================================================== the router


def build_orgs_router(deps: OrgDeps) -> APIRouter:
    router = APIRouter()

    def _bearer(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        return authorization[len("Bearer ") :].strip()

    @router.post("/orgs")
    def create_org(
        payload: dict = Body(...), authorization: str | None = Header(default=None)
    ) -> dict:
        """Create an organization; the caller becomes primary owner and its first member."""
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="a name is required")
        try:
            seats = int(payload.get("seats_total") or SEATS_DEFAULT)
        except (TypeError, ValueError):
            seats = SEATS_DEFAULT
        seats = max(1, min(seats, SEATS_MAX))
        with deps.db() as c:
            caller = deps.account_for_token(c, _bearer(authorization))
            # An org is inferred from — and tied to — the creator's WORK email domain (the
            # self-serve "workspaces for acme.com" pattern), so the NEXT colleague to sign in
            # routes here with no extra step. A personal provider cannot own one: claiming
            # gmail.com would pool every unrelated Gmail user together, so it is refused here
            # rather than silently creating a domain-less org no colleague could ever find.
            dom = email_domain(str(caller["email"] or ""))
            if not dom or is_public_email_domain(dom):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"organizations need a work email — {dom or 'this address'} is a personal "
                        "provider and cannot own one; use it as an individual account instead"
                    ),
                )
            org_id = "org_" + secrets.token_hex(8)
            at = deps.now()
            c.execute(
                "INSERT INTO orgs (id, name, primary_owner, seats_total, active, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (org_id, name[:120], str(caller["id"]), seats, at),
            )
            c.execute(
                "INSERT INTO org_members (org_id, account_id, role, monthly_credit_cap, "
                "added_by, added_at, active) VALUES (?, ?, 'owner', 0, '', ?, 1)",
                (org_id, str(caller["id"]), at),
            )
            # Claim the domain now, at creation, from the email — never a separate "add domain"
            # step. This is the single fact that makes the next colleague's sign-in match here.
            c.execute(
                "INSERT INTO org_domains (org_id, domain, verified, added_by, added_at) "
                "VALUES (?, ?, 0, ?, ?) ON CONFLICT(org_id, domain) DO NOTHING",
                (org_id, dom, str(caller["id"]), at),
            )
            org = c.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone()
            view = _org_view(c, org, "owner")
        count("org_created_total", _props={"org_id": org_id})
        return view

    @router.get("/me/orgs")
    def my_orgs(authorization: str | None = Header(default=None)) -> dict:
        """The switcher's data: my orgs + my role, plus the orgs my email domain could join."""
        with deps.db() as c:
            caller = deps.account_for_token(c, _bearer(authorization))
            memberships = org_memberships(c, str(caller["id"]))
            names = {
                str(r["id"]): str(r["name"])
                for r in c.execute(
                    f"SELECT id, name FROM orgs WHERE id IN ({','.join('?' * len(memberships))})",
                    tuple(org_id for org_id, _ in memberships),
                ).fetchall()
            } if memberships else {}
            return {
                "orgs": [
                    {"id": org_id, "role": role, "name": names.get(org_id, org_id)}
                    for org_id, role in memberships
                ],
                "joinable": joinable_orgs(c, str(caller["email"] or ""), str(caller["id"])),
            }

    @router.get("/orgs/{org_id}")
    def org_detail(org_id: str, authorization: str | None = Header(default=None)) -> dict:
        with deps.db() as c:
            caller = deps.account_for_token(c, _bearer(authorization))
            role = _require_member(c, org_id, str(caller["id"]))
            return _org_view(c, _org_row(c, org_id), role)

    @router.post("/orgs/{org_id}/invites")
    def mint_invite(
        org_id: str,
        payload: dict = Body(default={}),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Mint an invite link. The plaintext token is returned ONCE and stored only hashed."""
        email = str(payload.get("email") or "").strip().lower()
        role = str(payload.get("role") or "member").strip() or "member"
        if role not in ("member", "admin"):
            # Owners are never minted by link: the owner role is the recovery anchor and is
            # granted only by an existing owner through the members route.
            raise HTTPException(status_code=400, detail="invites grant 'member' or 'admin'")
        try:
            days = float(payload.get("expires_days") or INVITE_TTL_DAYS_DEFAULT)
        except (TypeError, ValueError):
            days = INVITE_TTL_DAYS_DEFAULT
        days = max(0.01, min(days, INVITE_TTL_DAYS_MAX))
        with deps.db() as c:
            caller = deps.account_for_token(c, _bearer(authorization))
            _require_org_admin(c, org_id, str(caller["id"]))
            org = _org_row(c, org_id)
            token = "orginv_" + secrets.token_urlsafe(24)
            expires_at = deps.now() + days * 86_400
            c.execute(
                "INSERT INTO org_invites (token_hash, org_id, email, role, expires_at, "
                "created_by, used_by) VALUES (?, ?, ?, ?, ?, ?, '')",
                (_hash_invite(token), org_id, email, role, expires_at, str(caller["id"])),
            )
            return {
                "invite_token": token,
                "org_id": org_id,
                "org_name": str(org["name"]),
                "email": email,
                "role": role,
                "expires_at": expires_at,
            }

    @router.post("/orgs/join")
    def join_org(
        payload: dict = Body(...), authorization: str | None = Header(default=None)
    ) -> dict:
        """Join by invite token, or by email-domain match. ALWAYS the caller's own choice —
        this route is the only way a membership row for someone else's org comes to exist."""
        invite_token = str(payload.get("invite_token") or "").strip()
        org_id = str(payload.get("org_id") or "").strip()
        with deps.db() as c:
            caller = deps.account_for_token(c, _bearer(authorization))
            account_id = str(caller["id"])
            at = deps.now()
            if invite_token:
                inv = c.execute(
                    "SELECT * FROM org_invites WHERE token_hash = ?",
                    (_hash_invite(invite_token),),
                ).fetchone()
                if inv is None or inv["used_by"] or float(inv["expires_at"] or 0) < at:
                    # One answer for unknown, used and expired: an invite probe learns nothing.
                    raise HTTPException(status_code=404, detail="invalid or expired invite")
                bound = str(inv["email"] or "")
                if bound and bound != str(caller["email"] or "").strip().lower():
                    raise HTTPException(
                        status_code=403, detail="this invite was issued to a different email"
                    )
                org = _org_row(c, str(inv["org_id"]))
                _add_member(
                    c, org, account_id, role=str(inv["role"]), added_by=str(inv["created_by"]),
                    at=at,
                )
                c.execute(
                    "UPDATE org_invites SET used_by = ? WHERE token_hash = ?",
                    (account_id, _hash_invite(invite_token)),
                )
            elif org_id:
                # Domain path: the org must have CLAIMED this email's domain. No verification
                # beyond the string match — the deliberate 2026-08-18 posture.
                domain = str(caller["email"] or "").rsplit("@", 1)[-1].strip().lower()
                claimed = c.execute(
                    "SELECT 1 FROM org_domains WHERE org_id = ? AND domain = ?",
                    (org_id, domain),
                ).fetchone()
                if claimed is None:
                    raise HTTPException(status_code=404, detail="unknown organization")
                org = _org_row(c, org_id)
                _add_member(c, org, account_id, role="member", added_by="domain", at=at)
            else:
                raise HTTPException(
                    status_code=400, detail="an invite_token or an org_id is required"
                )
            view = _org_view(c, org, _member_role(c, str(org["id"]), account_id))
        count("org_join_total", via="invite" if invite_token else "domain")
        return view

    @router.post("/orgs/{org_id}/members/{member_id}")
    def update_member(
        org_id: str,
        member_id: str,
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Role change / per-member monthly cap / remove. Owner rows are immune to admins;
        the primary owner is immune to everyone — it is the org's recovery anchor."""
        with deps.db() as c:
            caller = deps.account_for_token(c, _bearer(authorization))
            caller_role = _require_org_admin(c, org_id, str(caller["id"]))
            org = _org_row(c, org_id)
            target = c.execute(
                "SELECT * FROM org_members WHERE org_id = ? AND account_id = ?",
                (org_id, member_id),
            ).fetchone()
            if target is None:
                raise HTTPException(status_code=404, detail="not a member")
            if str(target["role"]) == "owner" and caller_role != "owner":
                raise HTTPException(status_code=403, detail="only an owner may change an owner")

            updates: list[str] = []
            args: list = []
            if "role" in payload:
                role = str(payload.get("role") or "").strip()
                if role not in ROLES:
                    raise HTTPException(status_code=400, detail=f"role must be one of {ROLES}")
                if role != "owner" and member_id == str(org["primary_owner"]):
                    raise HTTPException(
                        status_code=403, detail="the primary owner cannot be demoted"
                    )
                if role == "owner" and caller_role != "owner":
                    raise HTTPException(status_code=403, detail="only an owner may grant owner")
                updates.append("role = ?")
                args.append(role)
            if "monthly_credit_cap" in payload:
                try:
                    cap = max(0, int(payload.get("monthly_credit_cap") or 0))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="cap must be a number") from None
                updates.append("monthly_credit_cap = ?")
                args.append(cap)
            if "active" in payload:
                active = 1 if payload.get("active") else 0
                if not active and member_id == str(org["primary_owner"]):
                    raise HTTPException(
                        status_code=403, detail="the primary owner cannot be removed"
                    )
                if active and not target["active"]:
                    _seat_available(c, org)  # re-adding takes a seat like any other join
                updates.append("active = ?")
                args.append(active)
            if not updates:
                raise HTTPException(status_code=400, detail="nothing to change")
            c.execute(
                f"UPDATE org_members SET {', '.join(updates)} "
                "WHERE org_id = ? AND account_id = ?",
                (*args, org_id, member_id),
            )
            return _org_view(c, org, caller_role)

    @router.post("/orgs/{org_id}/domains")
    def update_domains(
        org_id: str,
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Add or remove an allowed email domain. Free text for now — see the module note."""
        domain = _clean_domain(payload.get("domain"))
        remove = bool(payload.get("remove"))
        with deps.db() as c:
            caller = deps.account_for_token(c, _bearer(authorization))
            role = _require_org_admin(c, org_id, str(caller["id"]))
            org = _org_row(c, org_id)
            if remove:
                c.execute(
                    "DELETE FROM org_domains WHERE org_id = ? AND domain = ?", (org_id, domain)
                )
            else:
                # Same rule as create: a public provider can never be an org domain, however it is
                # added. Removing one is always fine (cleanup), so the check guards only the add.
                if is_public_email_domain(domain):
                    raise HTTPException(
                        status_code=400,
                        detail=f"{domain} is a public email provider — an organization cannot "
                        "claim it (only a domain your company owns routes teammates here)",
                    )
                c.execute(
                    "INSERT INTO org_domains (org_id, domain, verified, added_by, added_at) "
                    "VALUES (?, ?, 0, ?, ?) ON CONFLICT(org_id, domain) DO NOTHING",
                    (org_id, domain, str(caller["id"]), deps.now()),
                )
            return _org_view(c, org, role)

    @router.get("/orgs/{org_id}/usage")
    def org_usage(org_id: str, authorization: str | None = Header(default=None)) -> dict:
        """Month-to-date rollup by member, from the usage ledger's org_id column (E2)."""
        with deps.db() as c:
            caller = deps.account_for_token(c, _bearer(authorization))
            _require_org_admin(c, org_id, str(caller["id"]))
            _org_row(c, org_id)
            month = deps.month_key(deps.now())
            rows = c.execute(
                "SELECT u.account_id, a.email, COALESCE(SUM(u.credits), 0) AS credits, "
                "COALESCE(SUM(u.cost_usd), 0.0) AS cost_usd, COUNT(*) AS calls "
                "FROM usage u LEFT JOIN accounts a ON a.id = u.account_id "
                "WHERE u.org_id = ? AND u.month = ? GROUP BY u.account_id "
                "ORDER BY credits DESC",
                (org_id, month),
            ).fetchall()
            caps = {
                str(r["account_id"]): int(r["monthly_credit_cap"] or 0)
                for r in c.execute(
                    "SELECT account_id, monthly_credit_cap FROM org_members WHERE org_id = ?",
                    (org_id,),
                ).fetchall()
            }
            return {
                "org_id": org_id,
                "month": month,
                "members": [
                    {
                        "account_id": str(r["account_id"]),
                        "email": str(r["email"] or ""),
                        "credits": int(r["credits"] or 0),
                        "cost_usd": round(float(r["cost_usd"] or 0.0), 6),
                        "calls": int(r["calls"] or 0),
                        "monthly_credit_cap": caps.get(str(r["account_id"]), 0),
                    }
                    for r in rows
                ],
            }

    return router
