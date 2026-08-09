"""PlatformAccountsHttp — the accounts service over HTTP, called BY THE DAEMON.

Who makes this call is the point. It used to be the browser: an agent's page POSTed the user's
password straight to the accounts service and kept the returned token in ``localStorage``. That
meant every agent UI had to be told where the accounts service lived (which is why the address
ended up baked into the product's distribution profile), and it meant page JavaScript held a
credential it has no reason to hold.

Moving the exchange in here collapses both problems: the page asks the daemon to sign someone in
and is told yes or no.

FAILURES RAISE. A 401 is a wrong password and the person needs to read that; a connection error
is a service that is down and the person needs to read that too. Returning "not signed in" for
either would render a login form with no message, which looks exactly like a form that was never
submitted.
"""

from __future__ import annotations

import httpx

from agent_runtime.domain.account_session import AccountSession


class PlatformAccountsHttp:
    """:param api_base: the accounts service root ("" => this install has no accounts service)."""

    def __init__(self, api_base: str = "", timeout: float = 15.0):
        self._api_base = (api_base or "").strip().rstrip("/")
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self._api_base)

    async def login(self, email: str, password: str, signup: bool = False) -> AccountSession:
        if not self._api_base:
            raise RuntimeError(
                "this daemon has no accounts service configured, so nobody can sign in. Set "
                "accounts.api_base in agentd.config.json (or AGENTD_ACCOUNTS_URL)."
            )
        async with httpx.AsyncClient(base_url=self._api_base, timeout=self._timeout) as client:
            if signup:
                await self._post(client, "/signup", {"email": email, "password": password})
            body = await self._post(client, "/login", {"email": email, "password": password})

        token = str(body.get("token") or body.get("session") or "")
        if not token:
            raise RuntimeError("the accounts service accepted the login but returned no token")
        return AccountSession(
            token=token,
            # The service is the authority on both, but a login reply that omits the email is
            # still a valid login — fall back to what was typed rather than reporting nobody.
            email=str(body.get("email") or email),
            account_id=str(body.get("account_id") or body.get("accountId") or ""),
        )

    async def _post(self, client: httpx.AsyncClient, path: str, body: dict) -> dict:
        try:
            response = await client.post(path, json=body)
        except httpx.HTTPError as e:
            raise RuntimeError(f"could not reach the accounts service: {e}") from e
        if response.status_code >= 400:
            raise RuntimeError(f"{path.lstrip('/')} failed: {self._message(response)}")
        try:
            payload = response.json()
        except ValueError as e:
            raise RuntimeError(f"the accounts service returned a non-JSON reply to {path}") from e
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _message(response: httpx.Response) -> str:
        """The service's own words where it gives them — "incorrect password" is worth showing;
        "HTTP 401" is not."""
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            for key in ("detail", "message", "error"):
                value = body.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        text = (response.text or "").strip()
        return text[:200] if text else f"HTTP {response.status_code}"
