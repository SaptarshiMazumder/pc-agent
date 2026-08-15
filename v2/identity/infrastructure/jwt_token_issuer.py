"""JWT issuing and verification, keyed by ``kid``.

DEFAULT ALGORITHM IS EdDSA (Ed25519) for a practical reason, not a cryptographic preference: an
Ed25519 signature is 64 bytes against RSA-2048's 256, and this token has to fit in a WebSocket
query string (browsers cannot set headers on a WebSocket) and be passed as a LiteLLM ``api_key``.
A ~350-character token is comfortable in both places; a ~900-character one is a liability.

VERIFICATION DOES NOT ASSUME THE ISSUING ALGORITHM. It reads ``kid`` from the header, finds that
key, and uses the algorithm the KEY declares. That is what makes the Cognito swap a URL change:
Cognito signs RS256, and a verifier hardcoded to EdDSA would have to be rewritten. The cost of
writing it this way now is one dictionary lookup.

WHY ``algorithms=[key.alg]`` AND NOT A LIST OF EVERYTHING WE SUPPORT: passing the full list is the
classic JWT confusion bug — an attacker picks the algorithm by choosing a header. Pinning to the
algorithm the resolved key was created with means the token cannot influence how it is checked.
"""

from __future__ import annotations

import json
import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from jwt import algorithms as jwt_algorithms

from identity.application.interfaces.key_store import KeyStore, SigningKey
from identity.domain.errors import IdentityConfigurationError, TokenExpired, TokenInvalid
from identity.domain.principal import Principal
from identity.domain.token import AccessClaims

#: Clock skew tolerance. Two machines that never talk to each other must agree on "expired", and
#: a minute is the usual allowance; without it a client whose clock runs slightly fast rejects a
#: token that was just minted for it.
LEEWAY_S = 60

EDDSA = "EdDSA"
RS256 = "RS256"
SUPPORTED = (EDDSA, RS256)


def _as_tuple(value) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return tuple(p for p in value.split(" ") if p)
    return tuple(str(v) for v in value)


class JwtTokenIssuer:
    def __init__(
        self,
        keys: KeyStore,
        *,
        issuer: str,
        access_ttl_s: int = 600,
        audience: tuple[str, ...] = ("agentd-daemon", "agentd-proxy"),
        clock=time.time,
    ):
        if not issuer:
            # An empty issuer means every deployment's tokens are interchangeable, which is
            # exactly the "same email, two stacks, silently different accounts" failure this
            # work exists to end. Refuse rather than issue something unverifiable-by-origin.
            raise IdentityConfigurationError("an issuer is required to mint tokens")
        self._keys = keys
        self._issuer = issuer
        self._ttl = int(access_ttl_s)
        self._audience = tuple(audience)
        self._clock = clock

    # -- issue ------------------------------------------------------------------------------

    def issue(self, principal: Principal, *, audience: tuple[str, ...] = ()) -> tuple[str, AccessClaims]:
        key = self._keys.active()
        now = float(self._clock())
        exp = now + self._ttl
        aud = tuple(audience or self._audience)
        jti = uuid.uuid4().hex
        payload = {
            "iss": self._issuer,
            "sub": principal.account_id,
            "aud": list(aud),
            "iat": int(now),
            "nbf": int(now),
            "exp": int(exp),
            "jti": jti,
            "sid": principal.session_id,
            "scope": " ".join(principal.scopes),
            "amr": list(principal.amr),
            "email": principal.email,
            "email_verified": bool(principal.email_verified),
            "ver": 1,
        }
        token = jwt.encode(
            payload,
            self._private(key),
            algorithm=key.alg,
            headers={"kid": key.kid},
        )
        claims = AccessClaims(
            account_id=principal.account_id,
            email=principal.email,
            email_verified=principal.email_verified,
            scopes=principal.scopes,
            amr=principal.amr,
            session_id=principal.session_id,
            issuer=self._issuer,
            audience=aud,
            issued_at=now,
            expires_at=exp,
            jti=jti,
        )
        return token, claims

    # -- verify -----------------------------------------------------------------------------

    def verify(self, token: str) -> AccessClaims:
        if not token or token.count(".") != 2:
            raise TokenInvalid("not a JWT")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise TokenInvalid(f"unreadable token header: {e}") from e

        kid = str(header.get("kid") or "")
        key = self._verification_key(kid)
        if key is None:
            # An unknown kid is the ONE case that must fail closed rather than fall back to
            # trying every key: it is what a token signed by a different deployment looks like.
            raise TokenInvalid(f"unknown signing key '{kid}'")

        try:
            payload = jwt.decode(
                token,
                self._public(key),
                algorithms=[key.alg],  # pinned to the KEY's algorithm — never the header's
                audience=list(self._audience),
                issuer=self._issuer,
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "sub", "iss"]},
            )
        except jwt.ExpiredSignatureError as e:
            raise TokenExpired("access token expired") from e
        except jwt.InvalidIssuerError as e:
            # Named explicitly because this is the "dev token presented to prod" case, and a
            # generic "invalid token" would send someone hunting for a signing bug for an hour.
            raise TokenInvalid(
                f"token was issued by another deployment (expected issuer {self._issuer})"
            ) from e
        except jwt.PyJWTError as e:
            raise TokenInvalid(str(e) or "invalid token") from e

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

    # -- jwks -------------------------------------------------------------------------------

    def public_jwks(self) -> dict:
        """Every live public key. Consumed by the daemon and the model proxy (P2)."""
        out = []
        for key in self._keys.verification_keys():
            jwk = self._to_jwk(key)
            if jwk:
                out.append(jwk)
        return {"keys": out}

    def _to_jwk(self, key: SigningKey) -> dict | None:
        try:
            public = self._public(key)
            algo = jwt_algorithms.get_default_algorithms().get(key.alg)
            if algo is None:
                return None
            raw = algo.to_jwk(public)
            jwk = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:  # noqa: BLE001 — one unconvertible key must not empty the whole JWKS
            return None
        jwk.update({"kid": key.kid, "alg": key.alg, "use": "sig"})
        return jwk

    # -- key material -----------------------------------------------------------------------

    def _verification_key(self, kid: str) -> SigningKey | None:
        for key in self._keys.verification_keys():
            if key.kid == kid:
                return key
        return None

    @staticmethod
    def _private(key: SigningKey):
        if not key.private_pem:
            raise IdentityConfigurationError(f"signing key '{key.kid}' has no private half")
        return serialization.load_pem_private_key(key.private_pem.encode("utf-8"), password=None)

    @staticmethod
    def _public(key: SigningKey):
        return serialization.load_pem_public_key(key.public_pem.encode("utf-8"))
