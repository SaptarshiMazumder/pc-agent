"""The composition root — the ONE place that decides which identity stack is in play.

    AGENTD_IDENTITY_PROVIDER   local | oidc            (unset => local)
    AGENTD_AUTH_ISSUER         https://accounts.<env>.<host>   REQUIRED to mint tokens
    AGENTD_AUTH_ACCESS_TTL_S   access-token lifetime, seconds  (default 600)
    AGENTD_AUTH_REFRESH_TTL_DAYS      sliding per-token life   (default 30)
    AGENTD_AUTH_REFRESH_FAMILY_DAYS   absolute per-login life  (default 90)
    AGENTD_AUTH_ALG            EdDSA | RS256                   (default EdDSA)
    AGENTD_AUTH_AUDIENCE       comma-separated                 (default agentd-daemon,agentd-proxy)
    AGENTD_IDENTITY_KEK        wraps signing keys at rest      (optional; see sqlite_key_store)

IT RAISES ON A PROVIDER NAME IT DOES NOT KNOW, and on a missing issuer. That is the same rule
``payments/main/payment_gateway_factory.py`` follows, for the same reason: a typo in
``AGENTD_IDENTITY_PROVIDER`` must not silently fall back to some other way of deciding who you
are. A service that refuses to start is a short outage; an auth mode nobody chose is a breach.

THE ISSUER IS REQUIRED because it is what stops a token minted by the dev stack from being
accepted by production. Defaulting it to something friendly would re-create, in a new form,
exactly the "same email, two stacks, silently different accounts" problem this work exists to end.
"""

from __future__ import annotations

import os
import sqlite3

from identity.application.interfaces.account_directory import AccountDirectory
from identity.application.interfaces.identity_provider import IdentityProvider
from identity.application.services.auth_service import AuthService
from identity.application.services.principal_service import PrincipalService
from identity.domain.errors import IdentityConfigurationError
from identity.infrastructure.jwt_token_issuer import SUPPORTED, JwtTokenIssuer
from identity.infrastructure.local_password_provider import LocalPasswordProvider
from identity.infrastructure.sqlite_identity_link_store import SqliteIdentityLinkStore
from identity.infrastructure.sqlite_key_store import SqliteKeyStore
from identity.infrastructure.sqlite_refresh_store import SqliteRefreshStore

LOCAL = "local"
OIDC = "oidc"
KNOWN = (LOCAL, OIDC)


def configured_provider_name() -> str:
    return (os.environ.get("AGENTD_IDENTITY_PROVIDER") or LOCAL).strip().lower() or LOCAL


def issuer() -> str:
    """The token issuer for THIS deployment. Empty means tokens cannot be minted."""
    return (os.environ.get("AGENTD_AUTH_ISSUER") or "").strip().rstrip("/")


def access_ttl_s() -> int:
    try:
        return max(60, int(os.environ.get("AGENTD_AUTH_ACCESS_TTL_S", "") or 600))
    except ValueError:
        return 600


def _days(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def audience() -> tuple[str, ...]:
    raw = os.environ.get("AGENTD_AUTH_AUDIENCE", "") or "agentd-daemon,agentd-proxy"
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def algorithm() -> str:
    alg = (os.environ.get("AGENTD_AUTH_ALG") or "EdDSA").strip()
    if alg not in SUPPORTED:
        raise IdentityConfigurationError(
            f"AGENTD_AUTH_ALG={alg!r} is not one of {', '.join(SUPPORTED)}"
        )
    return alg


def tokens_available() -> bool:
    """Can this deployment mint tokens at all?

    False (no issuer configured) is a SUPPORTED state, not an error: it is every install that has
    not been given a platform URL yet, and it is what keeps the legacy `sess_` path working
    unchanged while the JWT path is rolled out. The router degrades to legacy-only; it does not
    fail to start.
    """
    return bool(issuer())


def build_identity_provider(directory: AccountDirectory) -> IdentityProvider:
    name = configured_provider_name()
    if name == LOCAL:
        return LocalPasswordProvider(directory)
    if name == OIDC:
        # Deferred import for the same reason the Stripe adapter is: the OIDC adapter pulls in an
        # HTTP client the accounts image should not have to carry to run on passwords alone.
        from identity.infrastructure.oidc_provider import providers_from_env

        built = providers_from_env()
        if not built:
            raise IdentityConfigurationError(
                "AGENTD_IDENTITY_PROVIDER=oidc but AGENTD_OIDC_PROVIDERS names none. "
                "Refusing to start an external-login deployment with no external provider."
            )
        return built[0]
    raise IdentityConfigurationError(
        f"AGENTD_IDENTITY_PROVIDER={name!r} is not one of {', '.join(KNOWN)}. "
        "Refusing to start with an auth mode nobody chose."
    )


def external_providers() -> list:
    """Every configured OIDC provider, whatever the primary provider is.

    SEPARATE FROM `build_identity_provider` on purpose: the common deployment is passwords PLUS
    "sign in with Google", not one or the other. The primary provider owns the password form; this
    list owns the buttons above it. Returns [] when none are configured, which is why the local
    build shows no buttons without a single conditional in the UI.
    """
    try:
        from identity.infrastructure.oidc_provider import providers_from_env

        return providers_from_env()
    except Exception:  # noqa: BLE001 — a misconfigured optional provider must not break sign-in
        return []


def build_auth_service(
    conn: sqlite3.Connection, directory: AccountDirectory, org_resolver=None
) -> AuthService:
    """Assemble the whole stack around one live database connection.

    Built PER REQUEST, like ``CheckoutService`` in accounts' ``_apply_purchase``: the stores take
    a connection, so the caller's transaction is the unit of work and a failed login rolls back
    everything it touched.

    ``org_resolver`` (optional): ``account_id -> ((org_id, role), ...)`` — the caller's org
    memberships, minted into the access token's ``orgs`` claim. Accounts injects one bound to
    the same connection; every other caller passes nothing and gets today's org-less tokens.
    """
    iss = issuer()
    if not iss:
        raise IdentityConfigurationError(
            "AGENTD_AUTH_ISSUER is not set; this deployment cannot mint tokens"
        )
    keys = SqliteKeyStore(conn, alg=algorithm())
    return AuthService(
        provider=build_identity_provider(directory),
        principals=PrincipalService(directory, SqliteIdentityLinkStore(conn)),
        issuer=JwtTokenIssuer(
            keys, issuer=iss, access_ttl_s=access_ttl_s(), audience=audience()
        ),
        refresh=SqliteRefreshStore(
            conn,
            ttl_s=_days("AGENTD_AUTH_REFRESH_TTL_DAYS", 30) * 86_400,
            family_ttl_s=_days("AGENTD_AUTH_REFRESH_FAMILY_DAYS", 90) * 86_400,
        ),
        directory=directory,
        access_ttl_s=access_ttl_s(),
        audience=audience(),
        org_resolver=org_resolver,
    )
