"""The password provider — our own users table, unchanged.

BIT-FOR-BIT COMPATIBLE WITH WHAT ``accounts/app.py`` ALREADY DOES: PBKDF2-HMAC-SHA256, 200,000
rounds, a 16-byte salt stored as hex, comparison via ``compare_digest``. That is not an accident to
be tidied later — it is the reason nobody has to reset a password. Change any of those three
numbers and every existing hash stops verifying.

The account row is reached through the ``AccountDirectory`` port, so this class never writes SQL
against the money service's table. When ``pw_salt``/``pw_hash`` eventually migrate into a
credentials table owned by identity, only the adapter behind that port moves.

WHY THE SUBJECT IS THE ACCOUNT ID. An identity record needs a stable, provider-owned key. For a
hosted provider that is its ``sub`` claim; for our own table there is nothing more stable than the
account id itself — the email is exactly the mutable thing we refuse to key on.
"""

from __future__ import annotations

import hashlib
import secrets

from identity.application.interfaces.account_directory import AccountDirectory
from identity.application.interfaces.identity_provider import Assertion
from identity.domain.errors import AuthenticationFailed, IdentityConfigurationError

#: MUST match accounts/app.py. See the module note.
PBKDF2_ROUNDS = 200_000
MIN_PASSWORD_LEN = 8

PROVIDER_NAME = "local"


def hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return dk.hex()


def new_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16).hex()
    return salt, hash_password(password, salt)


class LocalPasswordProvider:
    """Email + password against our own accounts table."""

    name = PROVIDER_NAME
    supports_password = True

    def __init__(self, directory: AccountDirectory):
        self._directory = directory

    def authenticate(self, *, email: str, password: str) -> Assertion:
        clean = (email or "").strip().lower()
        record = self._directory.find_by_email(clean) if clean else None

        # CONSTANT-ISH WORK ON THE MISS PATH. Returning early for an unknown email makes the
        # endpoint answer noticeably faster than for a known one, and 200k PBKDF2 rounds is a
        # large enough difference to time from the outside — which turns the login form into an
        # account-enumeration oracle regardless of how carefully the error text is worded.
        stored_salt = record.password_salt if record else secrets.token_bytes(16).hex()
        stored_hash = record.password_hash if record else ""
        candidate = hash_password(password or "", stored_salt)
        ok = bool(record) and bool(stored_hash) and secrets.compare_digest(candidate, stored_hash)
        if not ok or record is None:
            raise AuthenticationFailed("invalid email or password")

        return Assertion(
            provider=self.name,
            subject=record.account_id,
            subject_is_account_id=True,
            email=record.email or clean,
            # Our own signup does not verify delivery, so this stays False and the address is
            # never used to auto-link anything. See PrincipalService.
            email_verified=False,
            amr=("pwd",),
        )

    def register(self, *, email: str, password: str) -> Assertion:
        clean = (email or "").strip().lower()
        if not clean or "@" not in clean:
            raise AuthenticationFailed("valid email required")
        if len(password or "") < MIN_PASSWORD_LEN:
            raise AuthenticationFailed(
                f"password must be at least {MIN_PASSWORD_LEN} characters"
            )
        salt, pw_hash = new_password(password)
        record = self._directory.create(email=clean, password_salt=salt, password_hash=pw_hash)
        return Assertion(
            provider=self.name,
            subject=record.account_id,
            subject_is_account_id=True,
            email=record.email,
            email_verified=False,
            amr=("pwd",),
        )

    def from_external_assertion(self, *, raw: str) -> Assertion:
        # Declared on the port so every provider is asked the question; there is genuinely no
        # external flow for a local password table. See interfaces/identity_provider.py.
        raise IdentityConfigurationError(
            "the local password provider has no external login flow; configure an OIDC provider"
        )
