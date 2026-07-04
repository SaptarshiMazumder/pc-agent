"""Ed25519 signing — ONE tiny surface shared by bundle verification (M4/M7) and
license files (M7). Keys travel as base64 raw 32-byte values (the `publisher_key`
in distribution.toml / a registry index); signatures as base64 raw 64-byte.

Publisher-side (us): generate_keypair + sign — used by `agentd bundle index --sign`
and `agentd license issue`. Client-side: verify only, against the PINNED key from
the distribution profile (installer-baked) or the registry index (v0 trust)."""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def generate_keypair() -> tuple[str, str]:
    """-> (private_b64, public_b64), both raw key bytes base64'd."""
    private = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
    public_b64 = base64.b64encode(private.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)).decode()
    return private_b64, public_b64


def sign(private_b64: str, message: bytes) -> str:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return base64.b64encode(key.sign(message)).decode()


def verify(public_b64: str, message: bytes, signature_b64: str) -> bool:
    """True iff the signature checks out. Malformed inputs are simply False —
    verification NEVER raises into caller logic (fail closed, message elsewhere)."""
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
        key.verify(base64.b64decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
