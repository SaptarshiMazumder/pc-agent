"""OIDC provider — Google, Microsoft, Cognito, or anything else that speaks the standard.

ONE ADAPTER FOR ALL OF THEM. These providers differ in their discovery URL and their client
credentials and in almost nothing else, so naming any of them in code would be inventing three
copies of one integration. Which provider a deployment uses is configuration; this class is the
protocol.

WHAT IT VALIDATES, AND WHY EACH CHECK IS LOAD-BEARING:

  * THE ID TOKEN'S SIGNATURE, against the provider's own JWKS. Without it, "sign in with Google"
    means "type any email you like" — the token is attacker-supplied.
  * `iss` AND `aud`. An id_token minted for a DIFFERENT application, signed by the same provider,
    is otherwise accepted. That is a real and commonly-missed cross-client attack.
  * `nonce`, against the value we generated. This is what binds the token to THIS login attempt
    and defeats replay of a captured id_token.
  * `email_verified`. It gates account LINKING (see PrincipalService): honouring an unverified
    address would let anyone claim an existing account by registering that address elsewhere.

The subject is the provider's `sub`, never the email — people change their email, and providers
reassign addresses.
"""

from __future__ import annotations

import logging
import os
import time

from identity.application.interfaces.identity_provider import Assertion
from identity.domain.errors import AuthenticationFailed, IdentityConfigurationError

log = logging.getLogger("identity.oidc")

_TIMEOUT_S = 6.0


class OidcProvider:
    """Authorization-code + PKCE against a standards-compliant provider."""

    supports_password = False

    def __init__(
        self,
        *,
        name: str,
        discovery_url: str,
        client_id: str,
        client_secret: str = "",
        redirect_uri: str = "",
        clock=time.time,
    ):
        if not (name and discovery_url and client_id):
            raise IdentityConfigurationError(
                "an OIDC provider needs a name, a discovery URL and a client id"
            )
        self.name = name
        self._discovery_url = discovery_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._clock = clock
        self._meta: dict | None = None

    # -- provider metadata ------------------------------------------------------------------

    def metadata(self) -> dict:
        """The provider's own discovery document, cached for the process.

        Fetched rather than configured so that a provider rotating its endpoints or its keys
        does not require a deploy from us — the same reasoning as our own platform discovery.
        """
        if self._meta is not None:
            return self._meta
        import httpx

        r = httpx.get(self._discovery_url, timeout=_TIMEOUT_S)
        if r.status_code != 200:
            raise IdentityConfigurationError(
                f"{self.name}: discovery returned HTTP {r.status_code}"
            )
        self._meta = r.json()
        return self._meta

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str = "") -> str:
        """Where to send the user's BROWSER to sign in."""
        from urllib.parse import urlencode

        meta = self.metadata()
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": redirect_uri or self._redirect_uri,
            "state": state,
            "nonce": nonce,
            # PKCE. Required even though we hold a client secret, because the DESKTOP flow has no
            # secret to hold: an installed app cannot keep one, so the code is bound to a
            # per-attempt verifier instead. One flow for both clients.
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{meta['authorization_endpoint']}?{urlencode(params)}"

    # -- the exchange -----------------------------------------------------------------------

    def exchange(self, *, code: str, code_verifier: str, nonce: str, redirect_uri: str = "") -> Assertion:
        """Authorization code -> a verified assertion about who signed in."""
        import httpx

        meta = self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "redirect_uri": redirect_uri or self._redirect_uri,
            "code_verifier": code_verifier,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret
        r = httpx.post(meta["token_endpoint"], data=data, timeout=_TIMEOUT_S)
        if r.status_code != 200:
            log.warning("%s token exchange http %s", self.name, r.status_code)
            raise AuthenticationFailed("sign-in could not be completed")
        id_token = str((r.json() or {}).get("id_token") or "")
        if not id_token:
            raise AuthenticationFailed("the provider returned no id_token")
        return self._assert_id_token(id_token, nonce=nonce)

    def _assert_id_token(self, id_token: str, *, nonce: str) -> Assertion:
        import jwt
        from jwt import PyJWK

        meta = self.metadata()
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as e:
            raise AuthenticationFailed("unreadable id_token") from e

        import httpx

        jwks = httpx.get(meta["jwks_uri"], timeout=_TIMEOUT_S).json()
        jwk = next((k for k in (jwks or {}).get("keys", []) if k.get("kid") == header.get("kid")), None)
        if jwk is None:
            raise AuthenticationFailed("the id_token was signed by an unknown key")
        try:
            claims = jwt.decode(
                id_token,
                PyJWK.from_dict(jwk).key,
                algorithms=[jwk.get("alg") or header.get("alg")],
                audience=self._client_id,   # minted for US, not another client of this provider
                issuer=meta.get("issuer"),
                leeway=60,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as e:
            raise AuthenticationFailed(f"the provider's id_token failed validation: {e}") from e

        # Binds the token to THIS login attempt. A captured id_token replayed into a fresh flow
        # carries the old nonce and is refused here.
        if nonce and str(claims.get("nonce") or "") != nonce:
            raise AuthenticationFailed("id_token nonce mismatch")

        return Assertion(
            provider=self.name,
            subject=str(claims.get("sub") or ""),
            email=str(claims.get("email") or "").strip().lower(),
            email_verified=bool(claims.get("email_verified")),
            amr=(self.name,),
        )

    # -- IdentityProvider surface -----------------------------------------------------------

    def authenticate(self, *, email: str, password: str) -> Assertion:
        raise IdentityConfigurationError(
            f"{self.name} is an external provider; there is no password to check here"
        )

    def register(self, *, email: str, password: str) -> Assertion:
        raise IdentityConfigurationError(
            f"{self.name} is an external provider; accounts are created on first sign-in"
        )

    def from_external_assertion(self, *, raw: str) -> Assertion:
        """Validate an id_token we already hold (no nonce binding available)."""
        return self._assert_id_token(raw, nonce="")


def providers_from_env() -> list[OidcProvider]:
    """Build every configured OIDC provider. DATA, not code.

        AGENTD_OIDC_PROVIDERS   comma-separated names, e.g. "google,microsoft"
        AGENTD_OIDC_<NAME>_DISCOVERY / _CLIENT_ID / _CLIENT_SECRET / _REDIRECT_URI

    Adding Microsoft is four environment variables and a redeploy of nothing — the sign-in UI
    renders whatever the discovery document advertises.
    """
    names = [n.strip() for n in (os.environ.get("AGENTD_OIDC_PROVIDERS", "") or "").split(",") if n.strip()]
    out: list[OidcProvider] = []
    for name in names:
        key = name.upper().replace("-", "_")
        out.append(
            OidcProvider(
                name=name,
                discovery_url=os.environ.get(f"AGENTD_OIDC_{key}_DISCOVERY", ""),
                client_id=os.environ.get(f"AGENTD_OIDC_{key}_CLIENT_ID", ""),
                client_secret=os.environ.get(f"AGENTD_OIDC_{key}_CLIENT_SECRET", ""),
                redirect_uri=os.environ.get(f"AGENTD_OIDC_{key}_REDIRECT_URI", ""),
            )
        )
    return out
