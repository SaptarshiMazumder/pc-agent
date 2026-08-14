"""OAuthService — sign in to a third-party service once, stay signed in, hand out live tokens.

ONE SERVICE, TWO CONSUMERS. A `[[mcp]]` server with `auth = "oauth:<name>"` needs a bearer token
on every connection; a private tool calling a REST API writes `${oauth:<name>}` in a header. Both
ask here, so refresh happens in one place and a user who signs in once is signed in for both.

THE FLOW, and why it is split in two calls. Authorization code with PKCE:

    begin()     -> the URL the user opens. A `state` is minted and held with its verifier.
    <the user signs in, the provider redirects to the daemon's /oauth/callback>
    complete()  -> the code is exchanged for tokens, which are stored.

Split because the middle step happens in a BROWSER, on the user's schedule, and may not happen at
all. A single blocking call would hold a request open for however long someone takes to find their
password, and would have nothing sensible to do when they close the tab.

PKCE ALWAYS, even where a client secret exists. The code that comes back travels through a
browser redirect to a loopback URL that any local process could race for; the verifier never
leaves this daemon, so a stolen code alone is worthless.

STATE IS CHECKED, not decorative. An unrecognised `state` on the callback is rejected outright —
that parameter is the only thing standing between "the user finished signing in" and "somebody
sent the user's browser to our callback with a code of their choosing".
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from agent_runtime.domain.oauth_connection import OAuthDecl, OAuthTokens

log = logging.getLogger("agentd")

#: How long a started sign-in stays valid. Long enough to find a password manager, short enough
#: that an abandoned attempt does not sit in memory all day.
FLOW_TTL_S = 600.0


@dataclass
class _Pending:
    """One sign-in in progress. Never persisted: if the daemon restarts mid-flow the user simply
    presses Connect again, which is a better outcome than a verifier surviving on disk."""

    agent_id: str
    name: str
    verifier: str
    redirect_uri: str
    decl: OAuthDecl
    started_at: float
    endpoints: dict = field(default_factory=dict)


class OAuthService:
    """:param http: an ``OAuthHttp``-shaped port — ``get_json(url)`` and
        ``post_form(url, data, auth)``. Injected so this layer never imports an HTTP client.
    :param store: a token store — ``load``/``save``/``delete``/``connected``.
    :param resolve_setting: ``(agent_id, "${X}" | literal) -> str``, so a declaration can name a
        ``[[settings]]`` key for its client id instead of hard-coding one.
    :param now: injected clock, so expiry is testable without sleeping.
    """

    def __init__(self, http, store, resolve_setting, now=time.time):
        self._http = http
        self._store = store
        self._resolve = resolve_setting
        self._now = now
        self._pending: dict[str, _Pending] = {}

    # ---- starting ----------------------------------------------------------

    async def begin(self, agent_id: str, decl: OAuthDecl, redirect_uri: str) -> str:
        """Mint a sign-in and return the URL for the user to open."""
        endpoints = await self._endpoints(agent_id, decl)
        client_id = self._resolve(agent_id, decl.client_id)
        if not client_id and not endpoints.get("registration_endpoint"):
            raise ValueError(
                f"'{decl.name}' needs a client_id: register an app with the provider and set it "
                f"in this agent's settings"
            )
        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(24)
        self._sweep()
        self._pending[state] = _Pending(
            agent_id=agent_id,
            name=decl.name,
            verifier=verifier,
            redirect_uri=redirect_uri,
            decl=decl,
            started_at=self._now(),
            endpoints=endpoints,
        )
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        }
        if decl.scopes:
            params["scope"] = " ".join(decl.scopes)
        return f"{endpoints['authorization_endpoint']}?{urlencode(params)}"

    # ---- finishing ---------------------------------------------------------

    async def complete(self, state: str, code: str) -> dict:
        """Exchange the authorization code for tokens and store them.

        Raises on an unknown or expired ``state``. That is the CSRF check and the reason this
        cannot be lenient: accepting a callback nobody started means accepting a code from
        whoever sent the browser here.
        """
        pending = self._pending.pop(state, None)
        if pending is None:
            raise ValueError("unknown or already-used sign-in — start it again from the agent")
        if self._now() - pending.started_at > FLOW_TTL_S:
            raise ValueError("this sign-in expired — start it again from the agent")
        if not code:
            raise ValueError("the provider returned no authorization code")

        agent_id, decl = pending.agent_id, pending.decl
        payload = await self._http.post_form(
            pending.endpoints["token_endpoint"],
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": pending.redirect_uri,
                "client_id": self._resolve(agent_id, decl.client_id),
                "code_verifier": pending.verifier,
            },
            self._client_secret(agent_id, decl),
        )
        tokens = self._tokens_from(payload)
        if not tokens.usable:
            raise RuntimeError(f"'{decl.name}' returned no access token: {payload}")
        self._store.save(agent_id, decl.name, tokens)
        log.info("oauth: '%s' connected for agent %s", decl.name, agent_id)
        return {"connected": True, "agentId": agent_id, "name": decl.name, "account": tokens.account}

    # ---- using -------------------------------------------------------------

    async def token(self, agent_id: str, decl: OAuthDecl) -> str:
        """A LIVE access token, refreshed if it is about to expire. Empty string if not connected.

        Empty rather than an exception because "not connected yet" is the normal state of a fresh
        install, and the caller's own message ("connect <name> in settings") is more useful than a
        stack trace. A refresh that FAILS does raise — that is a broken connection, not an absent
        one, and silently behaving like the second would send the user to reconnect something that
        is already connected.
        """
        tokens = self._store.load(agent_id, decl.name)
        if tokens is None or not tokens.usable:
            return ""
        if not tokens.stale(self._now()):
            return tokens.access_token
        if not tokens.refresh_token:
            # Expired with no way to renew: the honest state is disconnected, so say so once and
            # clear it rather than handing out a token that will 401.
            self._store.delete(agent_id, decl.name)
            log.info("oauth: '%s' expired for agent %s and cannot refresh", decl.name, agent_id)
            return ""
        endpoints = await self._endpoints(agent_id, decl)
        payload = await self._http.post_form(
            endpoints["token_endpoint"],
            {
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": self._resolve(agent_id, decl.client_id),
            },
            self._client_secret(agent_id, decl),
        )
        refreshed = self._tokens_from(payload)
        if not refreshed.usable:
            raise RuntimeError(f"could not refresh '{decl.name}': {payload}")
        # A provider that does not re-issue a refresh token means "keep the one you have".
        merged = OAuthTokens(
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or tokens.refresh_token,
            expires_at=refreshed.expires_at,
            scopes=refreshed.scopes or tokens.scopes,
            account=refreshed.account or tokens.account,
        )
        self._store.save(agent_id, decl.name, merged)
        return merged.access_token

    def status(self, agent_id: str, decls) -> list[dict]:
        """What this agent declared and whether each one is signed in."""
        out = []
        for decl in decls:
            tokens = self._store.load(agent_id, decl.name)
            out.append(
                {
                    "name": decl.name,
                    "connected": bool(tokens and tokens.usable),
                    "account": (tokens.account if tokens else ""),
                    "scopes": list(decl.scopes),
                }
            )
        return out

    def disconnect(self, agent_id: str, name: str) -> bool:
        return bool(self._store.delete(agent_id, name))

    def stored_token(self, agent_id: str, name: str) -> str:
        """A token WITHOUT refreshing — for callers that cannot await.

        The `${oauth:…}` substitution runs inside a plugin's synchronous `fetch`, so it gets this.
        A token that has gone stale returns empty and the request fails with the provider's own
        401, which is a fixable message; the refresh then happens on the next path that can await
        one. Better than blocking a plugin call on a token endpoint.
        """
        tokens = self._store.load(agent_id, name)
        return tokens.access_token if (tokens and tokens.usable and not tokens.stale(self._now())) else ""

    # ---- internals ---------------------------------------------------------

    async def _endpoints(self, agent_id: str, decl: OAuthDecl) -> dict:
        """Where to send the user and where to exchange the code.

        Explicit URLs win. Otherwise they are DISCOVERED from the declared server (RFC 8414),
        which is what lets a modern provider be three lines of toml instead of two URLs somebody
        has to find in a docs page and keep up to date.
        """
        if decl.authorize_url and decl.token_url:
            return {
                "authorization_endpoint": decl.authorize_url,
                "token_endpoint": decl.token_url,
            }
        if not decl.server:
            raise ValueError(
                f"'{decl.name}' needs either a `server` to discover, or both `authorize_url` "
                f"and `token_url`"
            )
        base = decl.server.rstrip("/")
        meta = await self._http.get_json(f"{base}/.well-known/oauth-authorization-server")
        if not meta.get("authorization_endpoint") or not meta.get("token_endpoint"):
            raise RuntimeError(
                f"'{decl.name}' published no usable OAuth metadata at {base} — declare "
                f"authorize_url and token_url explicitly"
            )
        return meta

    def _client_secret(self, agent_id: str, decl: OAuthDecl) -> str:
        return self._resolve(agent_id, decl.client_secret)

    def _tokens_from(self, payload: dict) -> OAuthTokens:
        expires_in = payload.get("expires_in")
        scope = payload.get("scope") or ""
        return OAuthTokens(
            access_token=str(payload.get("access_token") or ""),
            refresh_token=str(payload.get("refresh_token") or ""),
            # Absolute, computed once — see OAuthTokens.
            expires_at=(self._now() + float(expires_in)) if expires_in else 0.0,
            scopes=tuple(scope.split()) if isinstance(scope, str) else (),
            account=str(payload.get("account") or payload.get("email") or ""),
        )

    def _sweep(self) -> None:
        """Drop abandoned sign-ins. Bounded memory, and a stale verifier is of no use to anyone."""
        cutoff = self._now() - FLOW_TTL_S
        for state in [s for s, p in self._pending.items() if p.started_at < cutoff]:
            self._pending.pop(state, None)


def _challenge(verifier: str) -> str:
    """S256: the provider only ever sees the hash, so the redirect cannot be replayed."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
