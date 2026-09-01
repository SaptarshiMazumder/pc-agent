"""The authorization-code + PKCE flow, server-side.

WHY THE DESKTOP USES A BROWSER AND NOT ITS OWN FORM. An installed application cannot keep a client
secret and must never see the user's provider password. The standard answer — and what every
desktop app that offers "Sign in with Google" does — is to open the SYSTEM browser, let the
provider authenticate there, and receive an authorization code back on a loopback listener. PKCE
binds that code to the one client instance that started the flow, so intercepting it is useless.

The payoff is that the desktop and the web run the SAME flow, and adding a provider becomes
configuration on the server rather than a client release.

WHAT IS HELD IN MEMORY, AND WHY THAT IS ENOUGH. A pending flow is a nonce, a state, and a code
challenge, alive for minutes. Losing them on a restart means an in-progress sign-in fails and the
user presses the button again. Persisting them would put short-lived secrets in the database for
no gain; the entries are capped and swept so a flood cannot grow memory without bound.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time

from identity.domain.errors import AuthenticationFailed

#: A browser round trip is minutes at most; anything older is abandoned.
FLOW_TTL_S = 600.0
MAX_PENDING = 500


def pkce_pair() -> tuple[str, str]:
    """(verifier, challenge). S256 only — the `plain` method defeats the point of PKCE."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class OAuthFlowStore:
    """Pending browser flows, keyed by `state`."""

    def __init__(self, *, clock=time.time):
        self._flows: dict[str, dict] = {}
        self._clock = clock

    def begin(self, *, provider: str, redirect_uri: str, code_challenge: str = "") -> dict:
        self._sweep()
        if len(self._flows) >= MAX_PENDING:
            raise AuthenticationFailed("too many sign-ins in progress; try again shortly")
        verifier, challenge = pkce_pair()
        flow = {
            # `state` is CSRF protection: the provider echoes it back, and a callback carrying a
            # state we never issued is somebody else's redirect landing on our endpoint.
            "state": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "verifier": verifier,
            # A PUBLIC client (the desktop) does its own PKCE and sends us only the challenge; a
            # confidential one lets us generate the pair. Either way the verifier never travels
            # with the code.
            "challenge": code_challenge or challenge,
            "provider": provider,
            "redirect_uri": redirect_uri,
            "at": float(self._clock()),
        }
        self._flows[flow["state"]] = flow
        return flow

    def consume(self, state: str) -> dict:
        """Take a pending flow, SINGLE USE. A replayed callback finds nothing."""
        self._sweep()
        flow = self._flows.pop(state or "", None)
        if flow is None:
            raise AuthenticationFailed("this sign-in link is no longer valid; start again")
        if float(self._clock()) - float(flow["at"]) > FLOW_TTL_S:
            raise AuthenticationFailed("this sign-in took too long; start again")
        return flow

    def _sweep(self) -> None:
        cutoff = float(self._clock()) - FLOW_TTL_S
        for key in [k for k, v in self._flows.items() if float(v["at"]) < cutoff]:
            self._flows.pop(key, None)
