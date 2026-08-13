"""HttpxOAuthHttp — the two HTTP calls an OAuth flow makes, and nothing else.

Deliberately tiny: metadata discovery is a GET, and both the code exchange and the refresh are a
form POST. Keeping it to those two verbs is what lets ``OAuthService`` be pure orchestration and
be tested without a socket.

ERRORS ARE RAISED, not folded into an empty dict. A token endpoint that answers 400 has told us
something specific — `invalid_client`, `invalid_grant` — and that sentence is the difference
between a user fixing their client id and a user pressing Connect forever.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")

TIMEOUT_S = 20.0


class HttpxOAuthHttp:
    async def get_json(self, url: str) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
            res = await client.get(url, headers={"Accept": "application/json"})
        if res.status_code >= 400:
            raise RuntimeError(f"GET {url} -> {res.status_code}")
        return res.json()

    async def post_form(self, url: str, data: dict, client_secret: str = "") -> dict:
        """Exchange or refresh. The secret goes in the BASIC AUTH header when there is one.

        `client_secret_basic` is what the spec requires a server to support and what most
        actually implement; sending the secret in the body as well is a common way to trip
        providers that reject duplicate client authentication.
        """
        import httpx

        auth = (data.get("client_id", ""), client_secret) if client_secret else None
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
            res = await client.post(
                url,
                data={k: v for k, v in data.items() if v},
                auth=auth,
                headers={"Accept": "application/json"},
            )
        try:
            payload = res.json()
        except ValueError:
            payload = {}
        if res.status_code >= 400:
            detail = payload.get("error_description") or payload.get("error") or res.text[:200]
            raise RuntimeError(f"token endpoint said {res.status_code}: {detail}")
        return payload
