"""Verify an access token WITHOUT calling the accounts service.

THIS MODULE IS THE POINT OF THE WHOLE MIGRATION. Today the model proxy calls ``GET /resolve``
before every uncached model call and the daemon calls it on every socket connect, so the accounts
service sits in the hot path of every user's every message — which is why ``custom_auth.py`` grew
a stale-serving grace window just to survive a blip, and why the plan lists Accounts as a hard
availability dependency. A signature check against a cached public key removes it entirely.

ONE IMPLEMENTATION, TWO IMAGES. The daemon and the model proxy are built separately, and the
obvious move — a copy in each — is how two copies of security code drift until only one of them
has the fix. So this lives in ``identity`` and both images ship the package.

SECURITY NOTES, each of which is a real attack if got wrong:

  * ALGORITHM IS TAKEN FROM THE RESOLVED KEY, never from the token header. Letting the token pick
    is the classic JWT confusion bug.
  * ISSUER AND AUDIENCE ARE ALWAYS CHECKED. The issuer check is what stops a token minted by the
    dev stack from being spent on production — the exact "same email, two deployments, silently
    different accounts" failure this work exists to end.
  * AN UNKNOWN ``kid`` TRIGGERS AT MOST ONE REFETCH PER WINDOW. Without that floor, a hostile
    client can make us hammer the JWKS endpoint by presenting tokens with random key ids.
  * NO NETWORK CALL ON THE HAPPY PATH. Keys are fetched once and cached; verification itself is
    pure CPU.

Fails soft on INFRASTRUCTURE and closed on CREDENTIALS: an unreachable JWKS endpoint falls back to
the disk cache (so a daemon that boots offline can still verify tokens it has keys for), but a
token we cannot verify is always rejected.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import jwt
from jwt import PyJWK

from identity.domain.errors import TokenExpired, TokenInvalid
from identity.domain.token import AccessClaims

log = logging.getLogger("identity.verify")

LEEWAY_S = 60
DEFAULT_TTL_S = 3600.0
#: Minimum seconds between JWKS fetches provoked by an unknown key id.
_REFETCH_FLOOR_S = 30.0
_TIMEOUT_S = 4.0


def _as_tuple(value) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return tuple(p for p in value.split(" ") if p)
    return tuple(str(v) for v in value)


def _claims_from(payload: dict) -> AccessClaims:
    return AccessClaims(
        account_id=str(payload.get("sub") or ""),
        email=str(payload.get("email") or ""),
        email_verified=bool(payload.get("email_verified")),
        scopes=_as_tuple(payload.get("scope")),
        amr=_as_tuple(payload.get("amr")),
        session_id=str(payload.get("sid") or ""),
        issuer=str(payload.get("iss") or ""),
        audience=_as_tuple(payload.get("aud")),
        issued_at=float(payload.get("iat") or 0),
        expires_at=float(payload.get("exp") or 0),
        jti=str(payload.get("jti") or ""),
    )


def looks_like_jwt(token: str) -> bool:
    """Cheap shape test, so a legacy opaque token is never sent down the JWT path (and vice
    versa). Not a security check — just routing."""
    return bool(token) and token.count(".") == 2 and not token.startswith("sess_")


class JwksVerifier:
    """Caches a deployment's public keys and verifies tokens against them."""

    def __init__(
        self,
        *,
        jwks_uri: str,
        issuer: str,
        audience: str | tuple[str, ...] = (),
        cache_ttl_s: float = DEFAULT_TTL_S,
        cache_path: str | Path | None = None,
        clock=time.time,
    ):
        self._uri = (jwks_uri or "").strip()
        self._issuer = (issuer or "").strip().rstrip("/")
        self._audience = (audience,) if isinstance(audience, str) and audience else tuple(audience or ())
        self._ttl = float(cache_ttl_s)
        self._cache_path = Path(cache_path) if cache_path else None
        self._clock = clock
        self._keys: dict[str, dict] = {}
        self._fetched_at = 0.0
        self._last_attempt = 0.0
        if self._cache_path:
            self._load_disk()

    @property
    def configured(self) -> bool:
        """False => this deployment has no JWKS to verify against; callers fall back to HTTP."""
        return bool(self._uri and self._issuer)

    # -- key material -----------------------------------------------------------------------

    def _load_disk(self) -> None:
        try:
            raw = json.loads(self._cache_path.read_text("utf-8"))  # type: ignore[union-attr]
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("_uri") != self._uri:
            # Keys cached for a DIFFERENT deployment are not a fallback; they are the wrong
            # trust anchor. Same rule as the platform-discovery cache.
            return
        self._keys = {k: v for k, v in (raw.get("keys") or {}).items()}
        self._fetched_at = float(raw.get("_at") or 0)

    def _save_disk(self) -> None:
        if not self._cache_path:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({"_uri": self._uri, "_at": self._fetched_at, "keys": self._keys}),
                "utf-8",
            )
        except OSError:  # pragma: no cover — a read-only state dir must not break verification
            pass

    def _absorb(self, doc: dict) -> None:
        keys = {}
        for jwk in (doc or {}).get("keys") or []:
            kid = str((jwk or {}).get("kid") or "")
            if kid:
                keys[kid] = jwk
        if keys:
            self._keys = keys
            self._fetched_at = float(self._clock())
            self._save_disk()

    def _should_fetch(self, kid: str) -> bool:
        now = float(self._clock())
        if now - self._last_attempt < _REFETCH_FLOOR_S:
            return False  # rate floor: an unknown kid must not become a fetch amplifier
        if not self._keys:
            return True
        if now - self._fetched_at > self._ttl:
            return True
        return bool(kid) and kid not in self._keys

    def _fetch_sync(self) -> dict | None:
        self._last_attempt = float(self._clock())
        try:
            import httpx

            r = httpx.get(self._uri, timeout=_TIMEOUT_S)
            return r.json() if r.status_code == 200 else None
        except Exception as e:  # noqa: BLE001 — fall back to whatever is cached
            log.warning("jwks fetch failed (%s): %s", type(e).__name__, e or "no detail")
            return None

    async def _fetch_async(self) -> dict | None:
        self._last_attempt = float(self._clock())
        try:
            import httpx

            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                r = await client.get(self._uri)
                return r.json() if r.status_code == 200 else None
        except Exception as e:  # noqa: BLE001
            log.warning("jwks fetch failed (%s): %s", type(e).__name__, e or "no detail")
            return None

    # -- verification -----------------------------------------------------------------------

    def _kid(self, token: str) -> str:
        try:
            return str(jwt.get_unverified_header(token).get("kid") or "")
        except jwt.PyJWTError as e:
            raise TokenInvalid(f"unreadable token header: {e}") from e

    def _decode(self, token: str, kid: str) -> AccessClaims:
        jwk = self._keys.get(kid)
        if jwk is None:
            raise TokenInvalid(f"unknown signing key '{kid}'")
        alg = str(jwk.get("alg") or "")
        if not alg:
            raise TokenInvalid(f"signing key '{kid}' does not declare an algorithm")
        try:
            payload = jwt.decode(
                token,
                PyJWK.from_dict(jwk).key,
                algorithms=[alg],  # from the KEY, never the token header
                audience=list(self._audience) if self._audience else None,
                issuer=self._issuer,
                leeway=LEEWAY_S,
                options={
                    "require": ["exp", "iat", "sub", "iss"],
                    "verify_aud": bool(self._audience),
                },
            )
        except jwt.ExpiredSignatureError as e:
            raise TokenExpired("access token expired") from e
        except jwt.InvalidIssuerError as e:
            raise TokenInvalid(
                f"token was issued by another deployment (expected {self._issuer})"
            ) from e
        except jwt.PyJWTError as e:
            raise TokenInvalid(str(e) or "invalid token") from e
        return _claims_from(payload)

    def verify(self, token: str) -> AccessClaims:
        if not self.configured:
            raise TokenInvalid("no JWKS configured")
        kid = self._kid(token)
        if self._should_fetch(kid):
            self._absorb(self._fetch_sync() or {})
        return self._decode(token, kid)

    async def averify(self, token: str) -> AccessClaims:
        if not self.configured:
            raise TokenInvalid("no JWKS configured")
        kid = self._kid(token)
        if self._should_fetch(kid):
            self._absorb(await self._fetch_async() or {})
        return self._decode(token, kid)
