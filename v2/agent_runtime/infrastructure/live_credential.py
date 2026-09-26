"""LiveCredential — the signed-in person's CURRENT access token, shared by a connection and every
run it starts.

WHY NOT THE ACCOUNT SNAPSHOT. A run pins the account dict it started with, and that dict carries
the access token of that moment. Access tokens are short-lived; the client renews them and pushes
the new one with `auth.update`, but the push replaced the CONNECTION's account and never reached
a run already in flight. A long build then died mid-turn with "access token expired", and
anything the run did as the user late in the turn (opening the agent's window to verify it)
presented a dead token.

ONE OBJECT PER CONNECTION, BY REFERENCE. The gateway creates it when the socket opens and pins it
on the connection's context; every run the connection starts inherits the same object through the
context snapshot, so `renew` on `auth.update` is seen by all of them at once.

SAME PERSON ONLY. A renewal is applied only when it is the same account. If the socket signs in as
someone else, runs already in flight keep the identity they started with — a run never changes
whose it is halfway through.
"""

from __future__ import annotations

import threading


class LiveCredential:
    def __init__(self, account_id: str, token: str) -> None:
        self._account_id = account_id
        self._token = token
        self._lock = threading.Lock()

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def token(self) -> str:
        with self._lock:
            return self._token

    def renew(self, account: dict | None) -> None:
        """Take the renewed token from a re-resolved account — if it is still the same person."""
        if not account or str(account.get("account_id") or "") != self._account_id:
            return
        token = str(account.get("session_token") or "")
        if token:
            with self._lock:
                self._token = token


__all__ = ["LiveCredential"]
