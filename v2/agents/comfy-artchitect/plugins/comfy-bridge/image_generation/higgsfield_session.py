"""Who we are to Higgsfield's developer API: the bearer, the user-agent, the workspace.

THE TOKEN IS A NAME. Higgsfield has no API key — its developer API (what its own CLI and MCP
speak, billed from the subscription's credits) takes the OAuth access token the CLI login
stores. Two ways in, both kept out of this code:
  - `${HIGGSFIELD_ACCESS_TOKEN}`: the host substitutes it per request. It lives ~24 h.
  - `${HIGGSFIELD_REFRESH_TOKEN}` + a `client_id` in config: this session mints an access token
    from Clerk's token endpoint when it has none or the one it holds is about to expire, and
    keeps that short-lived token in memory. The refresh token itself is never read here —
    it rides in the request body as a name the host fills in.

THE WORKSPACE IS DISCOVERED, not written down: the account's workspaces are listed and the
selected one (else the one it owns) is used, unless config names one.

A plain user-agent is set on purpose: Cloudflare in front of the API refuses library defaults.
"""

from __future__ import annotations

import time
from typing import Callable

from agent_runtime.infrastructure.net.outbound import fetch

API = "https://fnf-api-gw.higgsfield.ai/fnf/developer/v2alpha"
_TOKEN_URL = "https://clerk.higgsfield.ai/oauth/token"
_USER_AGENT = "comfy-penguin/0.1 (agentd)"
_RENEW_BEFORE_S = 300


class HiggsfieldSession:
    def __init__(self, settings: Callable[[], dict]) -> None:
        # Read per call, never cached: config is per run, one session serves every run.
        self._settings = settings
        self._minted = ""
        self._minted_until = 0.0
        self._workspace = ""

    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._bearer()}",
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "hf-workspace-id": self.workspace(),
        }

    def auth_only(self) -> dict:
        return {"Authorization": f"Bearer {self._bearer()}", "User-Agent": _USER_AGENT, "Accept": "application/json"}

    def usd_per_credit(self) -> float:
        value = self._settings().get("usd_per_credit")
        if not value:
            raise ValueError(
                "Higgsfield needs `usd_per_credit` in agent.toml [plugins.comfy-bridge.providers.higgsfield] "
                "(the plan's price divided by its monthly credits, e.g. 0.049 for Plus at $59/1200)"
            )
        return float(value)

    def workspace(self) -> str:
        configured = str(self._settings().get("workspace") or "")
        if configured:
            return configured
        if not self._workspace:
            res = fetch(API + "/account/workspaces", headers=self.auth_only(), timeout_s=30)
            if not res.ok:
                raise RuntimeError(f"Higgsfield would not list workspaces: {res.error or f'HTTP {res.status}: {res.text[:300]}'}")
            items = res.json().get("items") or []
            chosen = next((w for w in items if w.get("is_selected")), None) or next(
                (w for w in items if w.get("user_role") == "owner"), None
            )
            if not chosen:
                raise RuntimeError("Higgsfield account has no workspace to generate in")
            self._workspace = str(chosen["id"])
        return self._workspace

    # ---- the bearer -------------------------------------------------------------------------

    def _bearer(self) -> str:
        client_id = str(self._settings().get("client_id") or "")
        if not client_id:
            # The host fills the name in at request time; this code never sees the value.
            return "${HIGGSFIELD_ACCESS_TOKEN}"
        if time.time() > self._minted_until - _RENEW_BEFORE_S:
            self._mint(client_id)
        return self._minted

    def _mint(self, client_id: str) -> None:
        res = fetch(
            _TOKEN_URL,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _USER_AGENT},
            data=f"grant_type=refresh_token&refresh_token=${{HIGGSFIELD_REFRESH_TOKEN}}&client_id={client_id}",
            timeout_s=30,
        )
        if not res.ok:
            raise RuntimeError(f"Higgsfield token refresh failed: {res.error or f'HTTP {res.status}: {res.text[:300]}'}")
        body = res.json()
        self._minted = str(body["access_token"])
        self._minted_until = time.time() + float(body.get("expires_in") or 3600)
