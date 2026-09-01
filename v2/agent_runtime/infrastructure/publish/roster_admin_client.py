"""RosterAdminClient — the thin HTTP side of `agentd bundle roster pending|admit|revoke`.

The CLI used to BE the admission machinery: it read the table, signed the roster file with a local
keypair, uploaded, flipped rows. All of that now lives in the publish service, where the vaulted
root key and the parked packages are — so this client only asks and renders. Keeping it dumb is
the point: an admin's laptop holds a session token and nothing else worth stealing.

Auth is the admin's ordinary platform session token — the same credential `publish_agent` sends —
and WHO is an admin is the service's decision (its allowlist), never this client's.
"""

from __future__ import annotations

import json

ADMIN_PATH = "/registry/admin"


class RosterAdminError(RuntimeError):
    """A refusal or failure, with the service's own message."""


class RosterAdminClient:
    def __init__(self, service_url: str, token: str, timeout: float = 120.0):
        self._url = service_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    # ------------------------------------------------------------------ calls
    def pending(self) -> list[dict]:
        return list(self._call("GET", "pending").get("pending") or [])

    def admit(self, creator_ids: list[str] | None = None) -> str:
        body: dict = {"creator_ids": [i for i in (creator_ids or []) if i]}
        return str(self._call("POST", "admit", body).get("message") or "admitted.")

    def revoke(self, creator_id: str) -> str:
        return str(
            self._call("POST", "revoke", {"creator_id": creator_id}).get("message") or "revoked."
        )

    # ------------------------------------------------------------------ transport
    def _call(self, method: str, action: str, body: dict | None = None) -> dict:
        import httpx

        headers = {"Authorization": f"Bearer {self._token}"}
        url = f"{self._url}{ADMIN_PATH}/{action}"
        try:
            if method == "GET":
                response = httpx.get(url, headers=headers, timeout=self._timeout)
            else:
                response = httpx.post(url, headers=headers, json=body or {}, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise RosterAdminError(f"could not reach the publish service at {self._url}: {e}") from e
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = {}
        if response.status_code >= 400:
            message = str(payload.get("message") or response.text or "").strip()
            raise RosterAdminError(f"{response.status_code}: {message or 'the service refused.'}")
        return payload if isinstance(payload, dict) else {}
