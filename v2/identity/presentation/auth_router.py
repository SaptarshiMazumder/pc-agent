"""The /auth/* HTTP surface.

THE ONLY PLACE DOMAIN ERRORS BECOME STATUS CODES. Everything below the presentation layer raises
typed exceptions (``identity/domain/errors.py``); this file owns the single mapping. A second
caller — a CLI, a socket handler — reuses the exceptions and picks its own reporting, and no two
surfaces can drift on what a 401 means.

The router is built with a SERVICE FACTORY rather than a service, because a service is bound to a
database connection and a connection lives for one request. The accounts app passes a context
manager that opens its own ``_db()``, so identity never learns how accounts connects to anything.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Callable

from fastapi import APIRouter, Body, Header, HTTPException, Request

from identity.application.services.auth_service import AuthService
from identity.application.services.oauth_flow import OAuthFlowStore
from identity.domain.errors import (
    AccountDisabled,
    AuthenticationFailed,
    IdentityConfigurationError,
    RefreshReuseDetected,
    TokenExpired,
    TokenInvalid,
)

#: () -> context manager yielding an AuthService bound to a live connection.
ServiceFactory = Callable[[], AbstractContextManager[AuthService]]
#: The host app's per-IP limiter (accounts already has one; auth must not bring a second policy).
RateLimiter = Callable[[Request], None]


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization[len("Bearer ") :].strip()


def build_auth_router(
    make_service: ServiceFactory,
    *,
    rate_limit: RateLimiter | None = None,
    available: Callable[[], bool] = lambda: True,
    external_providers: Callable[[], list] = list,
) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    # Pending browser flows live for the life of the process (see OAuthFlowStore for why that is
    # the right storage for a value measured in minutes).
    flows = OAuthFlowStore()

    def _external(name: str):
        return next((p for p in external_providers() if p.name == name), None)

    def _guard(request: Request) -> None:
        if not available():
            # No issuer configured. A 501 rather than a 500: nothing is broken, this deployment
            # simply has not been given a platform identity yet and the legacy path still serves.
            raise HTTPException(
                status_code=501,
                detail="token auth is not configured on this deployment (AGENTD_AUTH_ISSUER unset)",
            )
        if rate_limit is not None:
            rate_limit(request)

    # EVERY HANDLER PUTS ITS try/except OUTSIDE `with make_service()`, and that is not a style
    # preference. The factory owns the transaction, so it has to SEE the domain exception to
    # decide whether the request's writes survive. Catching inside the `with` converts the
    # exception to an HTTPException before the transaction closes — and the one case where that
    # matters is refresh-token reuse, where the write being rolled back is the family revocation
    # that IS the response to a theft. Detected, reported, and silently undone.

    @router.post("/login")
    def login(request: Request, payload: dict = Body(...)) -> dict:
        _guard(request)
        try:
            with make_service() as service:
                pair = service.login(
                    email=str(payload.get("email") or ""),
                    password=str(payload.get("password") or ""),
                    client_id=str(payload.get("client_id") or "")[:64],
                    device_label=str(payload.get("device_label") or "")[:120],
                )
        except AuthenticationFailed as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        except AccountDisabled as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        return pair.as_response()

    @router.post("/register")
    def register(request: Request, payload: dict = Body(...)) -> dict:
        _guard(request)
        try:
            with make_service() as service:
                pair = service.register(
                    email=str(payload.get("email") or ""),
                    password=str(payload.get("password") or ""),
                    client_id=str(payload.get("client_id") or "")[:64],
                    device_label=str(payload.get("device_label") or "")[:120],
                )
        except AuthenticationFailed as e:
            # Validation failures (bad email, short password) and duplicate emails both land
            # here; the message distinguishes them for the UI's error map.
            detail = str(e)
            code = 409 if "already" in detail.lower() else 400
            raise HTTPException(status_code=code, detail=detail) from e
        except AccountDisabled as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        return pair.as_response()

    @router.post("/refresh")
    def refresh(request: Request, payload: dict = Body(...)) -> dict:
        _guard(request)
        token = str(payload.get("refresh_token") or "")
        try:
            with make_service() as service:
                pair = service.refresh_tokens(
                    refresh_token=token,
                    client_id=str(payload.get("client_id") or "")[:64],
                    device_label=str(payload.get("device_label") or "")[:120],
                )
        except RefreshReuseDetected as e:
            # 401 with a DISTINCT detail. The client must not retry — its whole family is
            # gone — so it has to be able to tell this apart from an ordinary expiry.
            raise HTTPException(
                status_code=401,
                detail="this session was ended for security reasons; please sign in again",
            ) from e
        except AccountDisabled as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except TokenInvalid as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        return pair.as_response()

    @router.post("/logout")
    def logout(request: Request, payload: dict = Body(default={})) -> dict:
        _guard(request)
        with make_service() as service:
            ended = service.logout(refresh_token=str((payload or {}).get("refresh_token") or ""))
        return {"ok": True, "ended": ended}

    @router.post("/logout-all")
    def logout_all(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _guard(request)
        with make_service() as service:
            claims = _verify(service, _bearer(authorization))
            revoked = service.logout_all(account_id=claims.account_id)
        return {"ok": True, "revoked": revoked}

    @router.get("/sessions")
    def sessions(request: Request, authorization: str | None = Header(default=None)) -> dict:
        _guard(request)
        with make_service() as service:
            claims = _verify(service, _bearer(authorization))
            return {"sessions": service.sessions(account_id=claims.account_id)}

    # ---- external providers (Google / Microsoft / Cognito), authorization code + PKCE ----
    #
    # Two endpoints, both thin. `/authorize` mints a flow and hands back the provider URL for the
    # BROWSER to visit; `/callback` turns the returned code into our own token pair. The desktop
    # uses the identical pair with a loopback `redirect_uri`, which is why there is no
    # desktop-specific path anywhere in this file.

    @router.post("/authorize")
    def authorize(request: Request, payload: dict = Body(...)) -> dict:
        _guard(request)
        name = str(payload.get("provider") or "").strip()
        redirect_uri = str(payload.get("redirect_uri") or "").strip()
        provider = _external(name)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"unknown provider '{name}'")
        try:
            flow = flows.begin(
                provider=name,
                redirect_uri=redirect_uri,
                code_challenge=str(payload.get("code_challenge") or "").strip(),
            )
            url = provider.authorization_url(
                state=flow["state"],
                nonce=flow["nonce"],
                code_challenge=flow["challenge"],
                redirect_uri=redirect_uri,
            )
        except AuthenticationFailed as e:
            raise HTTPException(status_code=429, detail=str(e)) from e
        except IdentityConfigurationError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        # The verifier goes back to the caller ONLY for a public client that generated its own
        # challenge; otherwise it stays here and the client never sees it.
        out = {"authorization_url": url, "state": flow["state"]}
        if not payload.get("code_challenge"):
            out["code_verifier"] = flow["verifier"]
        return out

    @router.post("/callback")
    def callback(request: Request, payload: dict = Body(...)) -> dict:
        _guard(request)
        try:
            flow = flows.consume(str(payload.get("state") or ""))
        except AuthenticationFailed as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        provider = _external(flow["provider"])
        if provider is None:  # pragma: no cover — provider removed mid-flow
            raise HTTPException(status_code=404, detail="provider is no longer configured")
        try:
            assertion = provider.exchange(
                code=str(payload.get("code") or ""),
                # A public client proves possession with the verifier IT generated; a confidential
                # one uses the one we kept. The code alone is never enough either way.
                code_verifier=str(payload.get("code_verifier") or "") or flow["verifier"],
                nonce=flow["nonce"],
                redirect_uri=flow["redirect_uri"],
            )
        except AuthenticationFailed as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        except IdentityConfigurationError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

        try:
            with make_service() as service:
                pair = service.login_external(
                    assertion=assertion,
                    client_id=str(payload.get("client_id") or "")[:64],
                    device_label=str(payload.get("device_label") or "")[:120],
                )
        except AccountDisabled as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        return pair.as_response()

    @router.get("/jwks.json")
    def jwks() -> dict:
        """PUBLIC, and necessarily so — this is what every verifier fetches. It contains only
        public halves; serving it openly is the entire point of asymmetric signing."""
        if not available():
            return {"keys": []}
        with make_service() as service:
            return service.jwks()

    return router


def _verify(service: AuthService, token: str):
    try:
        return service.verify_access(token)
    except TokenExpired as e:
        raise HTTPException(status_code=401, detail="access token expired") from e
    except TokenInvalid as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except IdentityConfigurationError as e:  # pragma: no cover — misconfiguration, not a caller
        raise HTTPException(status_code=500, detail=str(e)) from e
