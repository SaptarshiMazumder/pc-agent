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

from fastapi import APIRouter, Body, Header, HTTPException, Request, Response

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

#: The refresh token's home in COOKIE MODE — the browser client's whole session story.
#:
#: A web page must never hold the 30-day credential where its own JavaScript (or anyone's, via an
#: injected script) can read it, so a browser client sends ``{"cookie": true}`` and the token
#: lives in an HttpOnly cookie instead of the response body. Scoped to ``/auth`` so it travels to
#: exactly the endpoints that spend it and nowhere else; ``SameSite=None`` because the web client
#: and the accounts service are different origins. The DESKTOP RUNTIME never asks for this — it
#: keeps the token in its own state dir (platform_session.py) and cookie mode changes nothing
#: for it.
COOKIE_NAME = "agentd_refresh"
COOKIE_PATH = "/auth"
COOKIE_MAX_AGE_S = 30 * 86_400  # the refresh store's own sliding TTL (sqlite_refresh_store.py)


def _request_is_https(request: Request) -> bool:
    """What the BROWSER thinks the scheme is — the fact the cookie flags must follow.

    Behind the ALB the app itself always speaks plain HTTP; `X-Forwarded-Proto` (first hop) is
    the original scheme. Directly served (local dev), the request's own scheme answers.
    """
    proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return (proto or request.url.scheme) == "https"


def _cookie_flags(request: Request) -> dict:
    """`Secure + SameSite=None` on HTTPS; `SameSite=Lax` without `Secure` on plain HTTP.

    NOT a security downgrade toggle — it is what browsers enforce. A `Secure` cookie sent over
    http is silently dropped, and `SameSite=None` REQUIRES `Secure`, so on an http deployment
    the "correct" flags produce a cookie that never exists and a web sign-in that never sticks.
    Lax works there because the web client and the accounts service share the ALB hostname
    (ports are not part of a cookie's site), which is same-site by definition. An https
    deployment gets the full cross-site pair.
    """
    https = _request_is_https(request)
    return {"secure": https, "samesite": "none" if https else "lax"}


def _set_refresh_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE_S,
        path=COOKIE_PATH,
        httponly=True,
        **_cookie_flags(request),
    )


def _clear_refresh_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, httponly=True, **_cookie_flags(request))


def _cookie_answer(request: Request, response: Response, pair) -> dict:
    """The body a cookie-mode client gets: the pair WITHOUT its secret half, which just became
    a Set-Cookie header instead."""
    _set_refresh_cookie(request, response, pair.refresh_token)
    out = pair.as_response()
    out.pop("refresh_token", None)
    return out


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
    def login(request: Request, response: Response, payload: dict = Body(...)) -> dict:
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
        if payload.get("cookie"):
            return _cookie_answer(request, response, pair)
        return pair.as_response()

    @router.post("/register")
    def register(request: Request, response: Response, payload: dict = Body(...)) -> dict:
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
        if payload.get("cookie"):
            return _cookie_answer(request, response, pair)
        return pair.as_response()

    @router.post("/refresh")
    def refresh(request: Request, response: Response, payload: dict = Body(...)) -> dict:
        _guard(request)
        token = str(payload.get("refresh_token") or "")
        # Cookie mode: the body carries no token because the browser was never allowed to see
        # one. The cookie IS the session; rotation re-sets it below.
        cookie_mode = bool(payload.get("cookie")) or (not token and COOKIE_NAME in request.cookies)
        if not token:
            token = str(request.cookies.get(COOKIE_NAME) or "")
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
        if cookie_mode:
            return _cookie_answer(request, response, pair)
        return pair.as_response()

    @router.post("/derive")
    def derive(request: Request, payload: dict = Body(...)) -> dict:
        """Trade a LIVE access token for a session of this caller's own.

        For a window that was handed a token rather than signing in — it mints its own chain here,
        once, and then renews itself like any other client instead of being fed forever.

        Rate-limited with the rest of `/auth/*`: it issues a long-lived credential, so it belongs
        with the endpoints that do, not with the read-only ones.
        """
        _guard(request)
        token = str(payload.get("access_token") or "")
        try:
            with make_service() as service:
                pair = service.derive_session(
                    access_token=token,
                    client_id=str(payload.get("client_id") or "")[:64],
                    device_label=str(payload.get("device_label") or "")[:120],
                )
        except AccountDisabled as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except TokenInvalid as e:
            # Expired counts as invalid, and deliberately so: a dead token proves nothing, and
            # accepting one would make the access token's lifetime meaningless.
            raise HTTPException(status_code=401, detail=str(e)) from e
        return pair.as_response()

    @router.post("/logout")
    def logout(request: Request, response: Response, payload: dict = Body(default={})) -> dict:
        _guard(request)
        token = str((payload or {}).get("refresh_token") or "") or str(
            request.cookies.get(COOKIE_NAME) or ""
        )
        with make_service() as service:
            ended = service.logout(refresh_token=token)
        # Unconditionally: a logout that leaves the cookie behind leaves the browser signed in.
        _clear_refresh_cookie(request, response)
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
