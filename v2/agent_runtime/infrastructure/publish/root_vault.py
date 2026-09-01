"""DynamoRootKeyVault — the platform root key, wrapped, in the creators table.

WHY THE ROOT KEY MOVES AT ALL. It signs the roster, and the roster is what admits creators. As a
file on one operator's laptop it made admission a single-person, single-machine operation — no
other admin could approve anyone, and that one disk dying would have frozen admissions until the
key was restored from backup. Here it lives KMS-wrapped in DynamoDB: decrypted only in the
Lambda's memory inside a signing call, IAM-gated, every decrypt in CloudTrail.

THE EXPLICIT TRADE, stated where the code is: an attacker inside the AWS account can sign rosters
while inside. The offline file was immune to that. It is the standard trade every hosted store
makes, and the operator keeps the offline file as the recovery anchor — this vault is a COPY.

WHY THE CREATORS TABLE AND NOT A NEW STORE. One reserved row (``account_id = "__root__"``) reuses
the table, the KMS key, and the wrapping pattern the creator keys already have — no new infra to
apply, back up, or reason about. The reserved id cannot collide: real rows are keyed by account
ids issued by the accounts service, and real creator ids are ``c-<hash>``. The row's ``state`` is
``"root"``, which no publish path treats as listable, so the root row can never publish anything.

Uploaded once by the operator (``agentd bundle roster upload-root``), from the machine that holds
the offline file — the one action that still touches it.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")

ROOT_ROW_ID = "__root__"


class DynamoRootKeyVault:
    def __init__(self, table, decrypt=None, encrypt=None):
        """:param decrypt: ciphertext_b64 -> plaintext, from KmsEnvelopeSigner.decrypt. None =>
        the stored key is plaintext (a local/dev registry — stated by the caller, never assumed).
        :param encrypt: the inverse, needed only by ``store`` (the operator upload path)."""
        self._table = table
        self._decrypt = decrypt
        self._encrypt = encrypt

    # ------------------------------------------------------------------ port
    def private_key(self) -> str:
        row = self._row()
        stored = str((row or {}).get("private_key") or "")
        if not stored:
            raise RuntimeError(
                "the root key vault is empty - roster changes need the platform root key. "
                "Upload it once with: agentd bundle roster upload-root --root-key <keypair>"
            )
        return self._decrypt(stored) if self._decrypt else stored

    def public_key(self) -> str:
        row = self._row()
        return str((row or {}).get("public_key") or "")

    # ------------------------------------------------------------------ operator upload
    def store(self, private_b64: str, public_b64: str) -> None:
        """Write (or rotate) the wrapped root key. Runs on the OPERATOR's machine — the one place
        the plaintext file legitimately exists — with their AWS credentials."""
        stored = self._encrypt(private_b64) if self._encrypt else private_b64
        self._table.put_item(
            Item={
                "account_id": ROOT_ROW_ID,
                "creator_id": ROOT_ROW_ID,
                "name": "platform root key",
                "state": "root",  # not `listed`: nothing may ever publish as the root row
                "public_key": public_b64,
                "private_key": stored,
            }
        )
        log.info("root key stored in the vault (%s)", "wrapped" if self._encrypt else "PLAINTEXT")

    # ------------------------------------------------------------------ internals
    def _row(self) -> dict | None:
        response = self._table.get_item(Key={"account_id": ROOT_ROW_ID})
        item = response.get("Item") if isinstance(response, dict) else None
        return dict(item) if item else None
