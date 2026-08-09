"""Signers — sign a digest with a CREATOR's own ed25519 key, never with the platform root key.

TWO IMPLEMENTATIONS, and the split is about where the private key rests:

``DirectoryBundleSigner``  the key sits in the creators table as plaintext base64. Correct for a
                          local run, a test, or a single-operator registry.
``KmsEnvelopeSigner``      the stored field is KMS CIPHERTEXT. Decrypted per signing call, used,
                          and dropped. At rest the table holds nothing usable.

WHY ENVELOPE ENCRYPTION RATHER THAN KMS SIGNING. AWS KMS can sign, but not with Ed25519 — its
asymmetric signing keys are RSA and NIST ECC. The registry format, every installed client, and
``infrastructure/signing.py`` are all Ed25519, and changing that would mean re-pinning every client
that has ever installed the app. So KMS guards the key instead of holding it: encrypt at rest,
decrypt in memory to sign. The honest limit: during a signing call the key exists in the function's
memory. What this buys is that a leaked table dump is worthless without the KMS grant.

NEITHER CAN REACH THE ROOT KEY. It signs the roster — who is a creator — and lives offline in a
secret the deploy pipeline reads, not here. That separation is exactly what makes running publishing
as a public web service safe: this code proves "a listed creator signed this bundle" and can never
add a creator.
"""

from __future__ import annotations

import base64
import logging

from agent_runtime.infrastructure import signing

log = logging.getLogger("agentd")


class DirectoryBundleSigner:
    """Signs with the plaintext key stored in the creator directory."""

    def __init__(self, directory):
        self._directory = directory

    def sign(self, creator_id: str, message: bytes) -> str:
        private = self._directory.private_key(creator_id)
        if not private:
            raise ValueError(f"creator {creator_id} has no signing key")
        return signing.sign(private, message)

    def public_key(self, creator_id: str) -> str:
        return self._directory.public_key(creator_id)


class KmsEnvelopeSigner:
    """Signs with a key the directory stores as KMS ciphertext."""

    def __init__(self, directory, kms_client, key_id: str = ""):
        self._directory = directory
        self._kms = kms_client
        self._key_id = key_id

    def sign(self, creator_id: str, message: bytes) -> str:
        ciphertext = self._directory.private_key(creator_id)
        if not ciphertext:
            raise ValueError(f"creator {creator_id} has no signing key")
        private = self.decrypt(ciphertext)
        return signing.sign(private, message)

    def public_key(self, creator_id: str) -> str:
        return self._directory.public_key(creator_id)

    # ------------------------------------------------------------------ crypto
    def encrypt(self, private_b64: str) -> str:
        """Wrap a freshly minted key. Used as the directory's key_factory, so a private key is
        ciphertext from the moment it exists and is never written in the clear."""
        blob = self._kms.encrypt(KeyId=self._key_id, Plaintext=private_b64.encode("utf-8"))
        return base64.b64encode(blob["CiphertextBlob"]).decode("ascii")

    def decrypt(self, ciphertext_b64: str) -> str:
        blob = self._kms.decrypt(CiphertextBlob=base64.b64decode(ciphertext_b64))
        return blob["Plaintext"].decode("utf-8")

    def key_factory(self):
        """-> a () -> (stored_private, public) for DynamoCreatorDirectory.

        The 'private' half it returns is already ciphertext. The directory never sees plaintext, so
        no code path exists that could persist one.
        """

        def mint() -> tuple[str, str]:
            private_b64, public_b64 = signing.generate_keypair()
            return self.encrypt(private_b64), public_b64

        return mint
