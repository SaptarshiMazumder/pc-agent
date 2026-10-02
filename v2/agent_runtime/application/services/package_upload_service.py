"""PackageUploadService — the author's package reaches storage without passing through the service.

    reserve(token)       -> a size-bounded upload slot only this account can take
    claim(token, key)    -> the uploaded bytes, for PublishIntakeService.submit

WHY THIS EXISTS. The publish service is a Lambda behind a load balancer, and a load balancer caps
a Lambda request body at 1 MB. An agent with a built window is past that on its own, so a publish
carrying its package in the request was refused before any of the intake's checks ran. The package
now goes straight to private storage, and the publish request names the slot it went into.

BOTH DOORS AUTHENTICATE. A slot is bound to the account that reserved it, and only that account can
claim it: without the check on `claim`, anyone who learned a slot key could publish another
author's upload as themselves. The intake then authenticates the same token again for its own
decisions — two lookups of one session, which is the price of the intake not knowing slots exist.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.publish_intake import (
    BAD_REQUEST,
    UNAUTHORIZED,
    IntakeResult,
    UploadSlot,
)
from agent_runtime.application.services.publish_intake_service import MAX_PACKAGE_BYTES


class PackageUploadService:
    def __init__(self, authenticator, slots, max_bytes: int = MAX_PACKAGE_BYTES):
        """:param authenticator: an ``Authenticator`` — the same one the intake uses.
        :param slots: an ``UploadSlots``."""
        self._auth = authenticator
        self._slots = slots
        self._max_bytes = max_bytes

    def reserve(self, token: str) -> tuple[IntakeResult | None, UploadSlot | None]:
        """-> (refusal, None) or (None, the slot)."""
        refusal, account_id = self._account_id(token)
        if refusal:
            return refusal, None
        return None, self._slots.reserve(account_id, self._max_bytes)

    def claim(self, token: str, key: str) -> tuple[IntakeResult | None, bytes]:
        """-> (refusal, b'') or (None, the package bytes). The slot is consumed either way it
        succeeds: a package is published from the bytes returned here, never read twice."""
        refusal, account_id = self._account_id(token)
        if refusal:
            return refusal, b""
        package = self._slots.take(account_id, str(key or "").strip())
        if not package:
            return (
                IntakeResult(
                    BAD_REQUEST,
                    "no uploaded package under that key for this account — the upload did not "
                    "finish, expired, or was already published. Publish again.",
                ),
                b"",
            )
        return None, package

    def _account_id(self, token: str) -> tuple[IntakeResult | None, str]:
        account = self._auth.account(token)
        if not account:
            return (
                IntakeResult(
                    UNAUTHORIZED, "not signed in, or the session expired. Sign in again and retry."
                ),
                "",
            )
        account_id = str(account.get("account_id") or account.get("id") or "").strip()
        if not account_id:
            return IntakeResult(UNAUTHORIZED, "that session has no account."), ""
        return None, account_id
