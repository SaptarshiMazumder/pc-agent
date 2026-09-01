"""Signing-key material: generate a key pair, wrap it for storage, unwrap it for use.

EXTRACTED FROM `sqlite_key_store` WHEN POSTGRES ARRIVED, and the reason is worth stating: the
alternative was a second copy of the envelope-encryption logic in the Postgres adapter, and two
copies of "how a private key is protected at rest" is the kind of duplication that does not
announce itself when it drifts. A key wrapped by one adapter and unwrapped by the other must
agree byte for byte forever; the only way to guarantee that is for there to be one of them.

Storage-agnostic on purpose: nothing here knows what a row or a table is. The adapters decide
where the bytes live; this decides what the bytes are.

TWO MODES, AND THE ROW REMEMBERS WHICH ONE MADE IT. When `AGENTD_IDENTITY_KEK` is set the
private half is Fernet-wrapped before storage; otherwise it is stored as PEM. `wrap` returns
that flag so the caller can persist it PER ROW rather than inferring it from the environment at
read time — inferring means setting or unsetting the KEK later turns every existing key into
garbage: a total, silent sign-out with no way back.

Not requiring a KEK is deliberate. The private key lives in the same database as the password
hashes, so on a laptop a secret drawn from the same environment protects nothing and only adds a
way for local development to fail. Where the database is backed up somewhere the KEK is not —
the deployed case — it is exactly the protection that matters, so running without one warns once.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from identity.domain.errors import IdentityConfigurationError
from identity.infrastructure.jwt_token_issuer import EDDSA, RS256

log = logging.getLogger("identity.keys")

_warned_plaintext = False


def kek() -> bytes | None:
    """The key-encryption key, derived to Fernet's expected 32 urlsafe-base64 bytes.

    Derived rather than required verbatim so an operator can set any passphrase; SHA-256 of the
    secret is the standard cheap KDF here and is adequate for a value that is already
    high-entropy infrastructure config.
    """
    raw = (os.environ.get("AGENTD_IDENTITY_KEK") or "").strip()
    if not raw:
        return None
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def wrap(pem: str) -> tuple[str, bool]:
    """(what to store, whether it is encrypted). Store the flag with the row."""
    key = kek()
    if key is None:
        global _warned_plaintext
        if not _warned_plaintext:
            _warned_plaintext = True
            log.warning(
                "identity: signing keys stored UNENCRYPTED (AGENTD_IDENTITY_KEK is not set). "
                "Fine for local development; set it wherever the database is backed up."
            )
        return pem, False
    from cryptography.fernet import Fernet

    return Fernet(key).encrypt(pem.encode("utf-8")).decode("ascii"), True


def unwrap(stored: str, encrypted: bool) -> str:
    if not encrypted:
        return stored
    key = kek()
    if key is None:
        raise IdentityConfigurationError(
            "a signing key was stored encrypted but AGENTD_IDENTITY_KEK is not set — "
            "restore the key-encryption key, or rotate to a new signing key"
        )
    from cryptography.fernet import Fernet, InvalidToken

    try:
        return Fernet(key).decrypt(stored.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise IdentityConfigurationError(
            "AGENTD_IDENTITY_KEK does not decrypt the stored signing key"
        ) from e


def generate(alg: str) -> tuple[str, str]:
    """(private_pem, public_pem) for a fresh key pair."""
    if alg == EDDSA:
        private = ed25519.Ed25519PrivateKey.generate()
    elif alg == RS256:
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    else:
        raise IdentityConfigurationError(f"unsupported signing algorithm '{alg}'")
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem
