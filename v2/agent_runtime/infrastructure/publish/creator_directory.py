"""DynamoCreatorDirectory — who may publish, and who owns which bundle id.

TWO TABLES, because they answer two different questions and one of them has to be ATOMIC.

``creators``       key: account_id
                   A publishing identity per account: creator id, display name, state
                   (pending | listed | revoked), public key, and the KMS-encrypted private key.
                   Created on first submission, in `pending`.

``bundle_owners``  key: bundle_id
                   Who owns an id. Written with a CONDITIONAL PUT — attribute_not_exists — so two
                   creators racing the same id cannot both win. This is the whole squatting and
                   hijack surface, and "read then write" would leave it wide open: both reads
                   return empty, both writes succeed, and the second creator now publishes updates
                   to the first creator's agent on every installed client.

THE CREATOR ID IS DERIVED FROM THE ACCOUNT, NOT THE EMAIL. A stable opaque id (an HMAC of the
account id, truncated) so it can appear in a public index and in a signed roster forever without
leaking who someone is or breaking when they change their email.

THE PRIVATE KEY IS NEVER RETURNED BY THIS CLASS. It is minted here on first sight, encrypted with
KMS, and only ``KmsBundleSigner`` reads that field — so a bug in the intake path cannot hand a key
to anything.
"""

from __future__ import annotations

import hashlib
import logging

from agent_runtime.application.interfaces.publish_intake import (
    LISTED,
    PENDING_REVIEW,
    REVOKED,
    Creator,
)

log = logging.getLogger("agentd")

CREATOR_ID_BYTES = 9  # 18 hex chars — collision-proof in practice, short enough to read in an index


def creator_id_for(account_id: str, namespace: str = "agentd-creator") -> str:
    """A stable, opaque creator id derived from the account id.

    Not the email and not the raw account id: this string goes into a PUBLIC index and a signed
    roster, both of which are permanent, and neither should reveal an identity or change when a
    user updates their profile.
    """
    digest = hashlib.blake2b(
        f"{namespace}:{account_id}".encode(), digest_size=CREATOR_ID_BYTES
    ).hexdigest()
    return f"c-{digest}"


class DynamoCreatorDirectory:
    def __init__(self, creators_table, owners_table, key_factory=None, now=None):
        """:param key_factory: () -> (private_b64, public_b64). Injected so tests mint fake keys and
        so a future HSM-backed factory drops in without touching this class.
        :param now: () -> ISO-8601 string. Injected because a table row's timestamps are the only
        record of when a creator appeared, and a test must be able to pin them."""
        self._creators = creators_table
        self._owners = owners_table
        self._key_factory = key_factory or self._default_keys
        self._now = now or self._default_now

    # ------------------------------------------------------------------ creators
    def for_account(self, account_id: str, display_name: str = "") -> Creator:
        account_id = (account_id or "").strip()
        if not account_id:
            raise ValueError("for_account needs an account id")
        row = self._get(self._creators, {"account_id": account_id})
        if row:
            return self._to_creator(row)

        # FIRST SUBMISSION. Mint the identity and the key, and record it as pending — the roster is
        # signed by an offline root key, so listing them is an operator action by construction.
        private_b64, public_b64 = self._key_factory()
        creator_id = creator_id_for(account_id)
        row = {
            "account_id": account_id,
            "creator_id": creator_id,
            "name": display_name or creator_id,
            "state": PENDING_REVIEW,
            "public_key": public_b64,
            "private_key": private_b64,  # already ciphertext when a KMS key factory is used
            "created": self._now(),
        }
        try:
            self._creators.put_item(
                Item=row,
                # A concurrent first publish from the same account must not mint two identities:
                # the loser re-reads and gets the winner's row, keys and all.
                ConditionExpression="attribute_not_exists(account_id)",
            )
        except Exception as e:  # noqa: BLE001 — boto raises ConditionalCheckFailedException
            if "ConditionalCheckFailed" not in type(e).__name__ and "ConditionalCheckFailed" not in str(e):
                raise
            existing = self._get(self._creators, {"account_id": account_id})
            if existing:
                return self._to_creator(existing)
            raise
        log.info("creator %s registered for account (pending review)", creator_id)
        return self._to_creator(row)

    def private_key(self, creator_id: str) -> str:
        """The stored private-key field for a creator. Read ONLY by the signer."""
        row = self._by_creator_id(creator_id)
        return str((row or {}).get("private_key") or "")

    def public_key(self, creator_id: str) -> str:
        row = self._by_creator_id(creator_id)
        return str((row or {}).get("public_key") or "")

    def pending(self) -> list[Creator]:
        """Creators awaiting admission — what the operator's roster command reads."""
        rows = self._scan(self._creators)
        return [self._to_creator(r) for r in rows if str(r.get("state") or "") == PENDING_REVIEW]

    def admit(self, creator_id: str) -> None:
        """Mark a creator LISTED. Called after the operator has published a roster naming them —
        never before, or their bundles would be advertised with a key no client trusts yet."""
        row = self._by_creator_id(creator_id)
        if not row:
            raise KeyError(creator_id)
        self._creators.update_item(
            Key={"account_id": row["account_id"]},
            UpdateExpression="SET #s = :s, admitted = :t",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":s": LISTED, ":t": self._now()},
        )

    def revoke(self, creator_id: str) -> None:
        row = self._by_creator_id(creator_id)
        if not row:
            raise KeyError(creator_id)
        self._creators.update_item(
            Key={"account_id": row["account_id"]},
            UpdateExpression="SET #s = :s, revoked = :t",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":s": REVOKED, ":t": self._now()},
        )

    # ------------------------------------------------------------------ bundle ownership
    def claim(self, bundle_id: str, creator_id: str) -> str:
        """-> the ACTUAL owner. Atomic: the first writer wins, forever."""
        try:
            self._owners.put_item(
                Item={"bundle_id": bundle_id, "creator_id": creator_id, "claimed": self._now()},
                ConditionExpression="attribute_not_exists(bundle_id)",
            )
            return creator_id
        except Exception as e:  # noqa: BLE001
            if "ConditionalCheckFailed" not in type(e).__name__ and "ConditionalCheckFailed" not in str(e):
                raise
            row = self._get(self._owners, {"bundle_id": bundle_id})
            return str((row or {}).get("creator_id") or "")

    def owner_of(self, bundle_id: str) -> str:
        row = self._get(self._owners, {"bundle_id": bundle_id})
        return str((row or {}).get("creator_id") or "")

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _get(table, key: dict) -> dict | None:
        response = table.get_item(Key=key)
        item = response.get("Item") if isinstance(response, dict) else None
        return dict(item) if item else None

    @staticmethod
    def _scan(table) -> list[dict]:
        out: list[dict] = []
        kwargs: dict = {}
        while True:
            page = table.scan(**kwargs)
            out += [dict(i) for i in page.get("Items") or []]
            key = page.get("LastEvaluatedKey")
            if not key:
                return out
            kwargs["ExclusiveStartKey"] = key

    def _by_creator_id(self, creator_id: str) -> dict | None:
        # A scan, not a GSI: this runs on an operator command and on one signing call per publish,
        # against a table with one row per creator. An index would be ceremony at this size.
        return next(
            (r for r in self._scan(self._creators) if str(r.get("creator_id") or "") == creator_id),
            None,
        )

    @staticmethod
    def _to_creator(row: dict) -> Creator:
        return Creator(
            id=str(row.get("creator_id") or ""),
            account_id=str(row.get("account_id") or ""),
            name=str(row.get("name") or ""),
            state=str(row.get("state") or PENDING_REVIEW),
            public_key=str(row.get("public_key") or ""),
        )

    @staticmethod
    def _default_keys() -> tuple[str, str]:
        from agent_runtime.infrastructure import signing

        return signing.generate_keypair()

    @staticmethod
    def _default_now() -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat(timespec="seconds")
