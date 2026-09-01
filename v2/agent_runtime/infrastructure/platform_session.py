"""PlatformSession — the machine's ONE signed-in account, and the ONE thing that refreshes it.

THE ARCHITECTURE THIS REPLACES, because it kept breaking. Every open window used to run its own
TokenManager: its own copy of the refresh token, its own 8-minute timer, its own renewal against
an accounts service whose refresh tokens are single-use. N windows meant N clocks racing over a
credential that only one of them could rotate — the losers tripped the reuse detector, families
got revoked, users were signed out ten minutes after signing in, and a renewal that merely
timed out tore down a websocket and told the user their daemon had restarted.

Ordinary desktop apps solved this decades ago, and this file is that solution:

    * ONE holder.    The refresh token lives here, in one file, owned by the runtime.
    * ONE refresher. Renewal happens in exactly one place, under one lock, single-flight —
                     ten windows asking at once cause one refresh, not ten.
    * WINDOWS ASK.   A client never refreshes and never holds anything long-lived. It makes a
                     local HTTP request and gets an access token back; "re-auth" in a window
                     degrades to re-reading a local value, which cannot race anything.
    * LAZY.          No timer. Refresh happens when a token is ASKED FOR and the cached one is
                     near death. A machine nobody uses performs no refreshes at all.

ONE ACCOUNT PER MACHINE, deliberately. Every window the daemon serves shares one origin, so they
share one cookie jar and one storage — per-window accounts require per-window credentials, which
is precisely the machinery being deleted. Simultaneous multi-account is the WEB deployment's job,
where cookies genuinely isolate; a desktop switches accounts by signing out.

THE ANSWER IS TYPED, never silence. "Your credential is dead" (sign in again) and "the accounts
service is unreachable" (keep working, retry) used to look identical to clients, and every
anonymous-reconnect bug came from conflating them. `token()` returns a state name a client can
branch on; it never guesses.

NOT ENCRYPTED, same statement as FileTokenStore: 0600 in the daemon's own state dir, beside an
.env that already holds provider keys in plain text. The OS keychain is the real upgrade and is
a later, separate step.

HOSTED DAEMONS NEVER USE THIS. There identity is a property of each connection (many people, one
process); the composition root simply does not construct one, and the gateway's endpoints answer
404. This file is the DESKTOP's story.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

log = logging.getLogger("agentd")

#: Refresh when the access token has less than this long to live. Two minutes: long enough that
#: a token handed out is usable for a full request cycle, short enough that we refresh once per
#: ~8 minutes of continuous use — the cadence every window used to run separately.
RENEW_MARGIN_SEC = 120.0

#: The four states `token()` can answer with. Strings, not an enum, because they go over HTTP
#: verbatim and a client in another language branches on them by name.
SIGNED_IN = "ok"
SIGNED_OUT = "signed_out"
SESSION_EXPIRED = "session_expired"
UNREACHABLE = "accounts_unreachable"


class PlatformSession:
    """:param state_dir: the daemon's state directory — the file lives at its top level.
    :param api_base: the accounts service base URL, or '' when this deployment has none."""

    def __init__(self, state_dir, api_base: str):
        self._path = Path(state_dir) / "platform-session.json"
        self._api_base = (api_base or "").rstrip("/")
        self._lock = asyncio.Lock()
        self._session: dict | None = self._read()

    # ------------------------------------------------------------------ state
    @property
    def enabled(self) -> bool:
        return bool(self._api_base)

    def status(self) -> dict:
        """Who is signed in, without touching the network."""
        s = self._session
        if not s:
            return {"state": SIGNED_OUT}
        return {
            "state": SIGNED_IN,
            "email": str(s.get("email") or ""),
            "accountId": str(s.get("account_id") or ""),
        }

    # ------------------------------------------------------------------ the three operations
    async def login(self, email: str, password: str, signup: bool = False) -> dict:
        """Exchange credentials for a session and BECOME the machine's account.

        The window that called this never sees a token in the answer — signing in is a fact
        about the machine, and the window learns it the same way every other window does: by
        asking `token()`.
        """
        if not self.enabled:
            return {"state": UNREACHABLE, "error": "this daemon has no accounts service"}
        path = "/auth/register" if signup else "/auth/login"
        try:
            async with httpx.AsyncClient(base_url=self._api_base, timeout=15.0) as c:
                r = await c.post(path, json={"email": email, "password": password})
        except httpx.HTTPError as e:
            return {"state": UNREACHABLE, "error": f"accounts service unreachable: {e}"}
        if r.status_code != 200:
            detail = ""
            try:
                detail = str(r.json().get("detail") or "")
            except ValueError:
                pass
            return {"state": SIGNED_OUT, "error": detail or f"sign-in failed (HTTP {r.status_code})"}
        pair = r.json()
        self._store(pair)
        log.info("platform session: signed in as %s", self._session.get("email", "?"))
        return self.status()

    async def token(self) -> dict:
        """A usable access token, refreshing IF NEEDED — the endpoint every window lives on.

        SINGLE-FLIGHT: the lock means concurrent callers ride one refresh. The rotated refresh
        token is persisted before anyone gets the new access token, so a crash between the two
        cannot strand a session that the server has already rotated away from.
        """
        if self._session is None:
            return {"state": SIGNED_OUT}
        async with self._lock:
            s = self._session
            if s is None:  # signed out while we waited on the lock
                return {"state": SIGNED_OUT}
            if float(s.get("expires_at") or 0) - time.time() > RENEW_MARGIN_SEC:
                return self._token_answer(s)
            return await self._refresh_locked(s)

    async def adopt(self, refresh_token: str) -> dict:
        """Become the machine's session from a refresh token minted elsewhere — the one-time
        migration path for a desktop that signed in under the old per-window world. The token is
        validated by USING it: one refresh proves it, rotates it into this file, and fills in
        who it belongs to. A dead token leaves no session behind."""
        token = (refresh_token or "").strip()
        if not token:
            return {"state": SIGNED_OUT, "error": "no refresh token presented"}
        async with self._lock:
            self._session = {"access_token": "", "refresh_token": token, "expires_at": 0.0}
            answer = await self._refresh_locked(self._session)
        if answer.get("state") == SIGNED_IN:
            log.info("platform session: adopted a legacy session (%s)", answer.get("email", "?"))
        else:
            # _refresh_locked already deleted the file on a refused token; make sure a merely
            # unreachable accounts service does not leave a half-adopted session either.
            self._session = None
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
        return answer

    async def logout(self) -> dict:
        """Forget the session everywhere. Best-effort at the server — a revocation that cannot
        be delivered must not keep the machine signed in locally."""
        s, self._session = self._session, None
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            log.exception("platform session: could not delete %s", self._path)
        if s and s.get("refresh_token") and self.enabled:
            try:
                async with httpx.AsyncClient(base_url=self._api_base, timeout=10.0) as c:
                    await c.post("/auth/logout", json={"refresh_token": s["refresh_token"]})
            except httpx.HTTPError:
                log.warning("platform session: server-side logout undelivered (local session gone)")
        return {"state": SIGNED_OUT}

    # ------------------------------------------------------------------ internals
    async def _refresh_locked(self, s: dict) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self._api_base, timeout=15.0) as c:
                r = await c.post("/auth/refresh", json={"refresh_token": s.get("refresh_token", "")})
        except httpx.HTTPError as e:
            # THE NETWORK, NOT THE CREDENTIAL. Serve the old token if it still has life in it;
            # otherwise say exactly what is wrong. Deleting the session here would sign a user
            # out because their wifi blinked — the confusion this file exists to end.
            log.warning("platform session: accounts unreachable during refresh: %s", e)
            if float(s.get("expires_at") or 0) > time.time():
                return self._token_answer(s)
            return {"state": UNREACHABLE, "retryAfterSec": 30}
        if r.status_code in (400, 401, 403):
            # The server REFUSED the refresh — revoked, expired, or reused. This session can
            # never work again; keeping the file would retry a dead credential forever.
            log.warning("platform session: refresh refused (HTTP %s) — signed out", r.status_code)
            self._session = None
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
            return {"state": SESSION_EXPIRED}
        if r.status_code != 200:
            log.warning("platform session: refresh failed (HTTP %s)", r.status_code)
            if float(s.get("expires_at") or 0) > time.time():
                return self._token_answer(s)
            return {"state": UNREACHABLE, "retryAfterSec": 30}
        self._store(r.json(), previous=s)
        return self._token_answer(self._session)

    def _token_answer(self, s: dict) -> dict:
        return {
            "state": SIGNED_IN,
            "accessToken": str(s.get("access_token") or ""),
            "expiresAt": float(s.get("expires_at") or 0),
            "email": str(s.get("email") or ""),
            "accountId": str(s.get("account_id") or ""),
        }

    def _store(self, pair: dict, previous: dict | None = None) -> None:
        prev = previous or {}
        expires_in = float(pair.get("expires_in") or 0)
        self._session = {
            "access_token": str(pair.get("access_token") or ""),
            # A refresh answer may rotate the refresh token or keep it; absent means keep.
            "refresh_token": str(pair.get("refresh_token") or prev.get("refresh_token") or ""),
            "expires_at": time.time() + expires_in if expires_in else float(pair.get("expires_at") or 0),
            "email": str(pair.get("email") or prev.get("email") or ""),
            "account_id": str(pair.get("account_id") or prev.get("account_id") or ""),
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._session, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass  # Windows: the ACL of the profile dir is the protection

    def _read(self) -> dict | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) and raw.get("refresh_token") else None
        except (OSError, ValueError):
            return None
