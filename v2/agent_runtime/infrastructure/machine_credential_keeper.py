"""MachineCredentialKeeper — keeps a DESKTOP daemon's connections holding a live access token.

WHY. Every connection carries a LiveCredential that the runs it starts share (live_credential.py),
and model calls, platform tools and the GPU service all pay with it. On the WEB the window renews
it: its client pushes each new token with `auth.update`. On the DESKTOP no window refreshes
anything — the daemon holds the machine's one session and IS the refresher (platform_session.py)
— so nothing ever pushed, the credential kept the token it was opened with, and an hour later
every turn on that window died with "access token expired" while the daemon sat on a fresh one.

WHAT IT DOES. Tracks the connections that belong to the machine's own account and, once a
minute while any are open, asks the platform session for its token. That call is lazy and
single-flight: free while the cached token has life left, one refresh when it is near death. A
fresh token is handed to every tracked credential — the same object in-flight runs hold, so a
three-hour build keeps paying with a live token without any window taking part.

SAME PERSON ONLY, by the credential's own rule: a connection that presented somebody else's
session is never renewed with the machine's token.

HOSTED DAEMONS NEVER BUILD ONE. There is no machine session on a hosted daemon, and the web's
`auth.update` path is left exactly as it was.
"""

from __future__ import annotations

import asyncio
import logging
import weakref

from agent_runtime.infrastructure.live_credential import LiveCredential

log = logging.getLogger("agentd")

CHECK_EVERY_S = 60.0


class MachineCredentialKeeper:
    def __init__(self, platform_session, check_every_s: float = CHECK_EVERY_S) -> None:
        self._session = platform_session
        self._every = check_every_s
        self._tracked: weakref.WeakSet[LiveCredential] = weakref.WeakSet()

    def track(self, credential: LiveCredential) -> None:
        self._tracked.add(credential)

    def untrack(self, credential: LiveCredential) -> None:
        self._tracked.discard(credential)

    async def renew_now(self) -> None:
        """Ask the machine session for its token and hand it to every tracked connection."""
        if not self._tracked:
            return
        answer = await self._session.token()
        if answer.get("state") != "ok":
            log.warning("machine session did not answer ok while renewing connections: %s",
                        answer.get("state"))
            return
        account = {
            "account_id": str(answer.get("accountId") or ""),
            "session_token": str(answer.get("accessToken") or ""),
        }
        for credential in list(self._tracked):
            credential.renew(account)

    async def run(self) -> None:
        """Forever: renew before any tracked token can lapse. Cancelled with the gateway."""
        while True:
            await asyncio.sleep(self._every)
            try:
                await self.renew_now()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a failed renewal is logged and retried next minute
                log.warning("renewing machine connections failed", exc_info=True)


__all__ = ["MachineCredentialKeeper"]
