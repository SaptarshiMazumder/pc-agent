"""AccountsAuthenticator — the author's session token, resolved by the accounts service.

THE SAME TOKEN, THE SAME ENDPOINT the daemon already uses (``GET /resolve`` with a bearer token). No
second credential and no publish-specific key: an author signs in once, and that session both
authorises their model calls and identifies them as a creator. A separate publishing credential
would be one more thing to issue, rotate and lose.

FAIL CLOSED, ALWAYS. An unreachable accounts service returns None, which the intake service turns
into 401. That is deliberately NOT fail-open: everywhere else in this codebase an accounts outage
degrades gracefully, but here the answer decides whose name goes on a public artifact signed with the
platform's certificate. "We could not check who you are, so we published it as you" is not a
tradeoff worth making for uptime.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


class AccountsAuthenticator:
    def __init__(self, api_base: str, http=None, timeout: float = 5.0):
        self._base = (api_base or "").rstrip("/")
        self._http = http
        self._timeout = timeout

    def account(self, token: str) -> dict | None:
        token = (token or "").strip()
        if not token or not self._base:
            return None
        try:
            response = self._get(
                f"{self._base}/resolve", headers={"Authorization": f"Bearer {token}"}
            )
        except Exception as e:  # noqa: BLE001 — any transport failure is "unauthorized"
            log.warning("publish auth: cannot reach accounts (%s)", e)
            return None
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def _get(self, url: str, headers: dict):
        if self._http is not None:
            return self._http.get(url, headers=headers, timeout=self._timeout)
        import httpx

        return httpx.get(url, headers=headers, timeout=self._timeout)
