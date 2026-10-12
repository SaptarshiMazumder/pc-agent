"""Operator-only signing metadata migration. Plain private keys never enter output.

Use --root-key for the original offline root. --aws-creators/--aws-owners copy
existing tables, decrypting KMS ciphertext in memory and rewrapping with Fernet.
Does not write to/delete AWS tables, rotate keys or change the registry index.
"""

import argparse
import json
from pathlib import Path

from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.publish.creator_directory import DynamoCreatorDirectory
from agent_runtime.infrastructure.publish.signer import KmsEnvelopeSigner
from agent_runtime.infrastructure.publish.sqlite_registry import adapters


def import_rows(database, cipher, rows, owners, decrypt):
    prepared = []
    for original in rows:
        row = dict(original)
        private = decrypt(row["private_key"])
        if not signing.verify(row["public_key"], b"migration", signing.sign(private, b"migration")):
            raise ValueError("signing key pair mismatch")
        row["private_key"] = cipher.encrypt(private.encode()).decode()
        prepared.append(row)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in prepared:
            existing = db.execute(
                "SELECT document FROM creators WHERE account=?", (row["account_id"],)
            ).fetchone()
            if existing and json.loads(existing[0])["public_key"] != row["public_key"]:
                raise ValueError("refusing to replace an existing creator identity")
            db.execute(
                "INSERT OR REPLACE INTO creators VALUES (?,?,?)",
                (row["account_id"], row["creator_id"], json.dumps(row)),
            )
        for row in owners:
            existing = db.execute(
                "SELECT creator FROM owners WHERE bundle=?", (row["bundle_id"],)
            ).fetchone()
            if existing and existing[0] != row["creator_id"]:
                raise ValueError("refusing bundle ownership transfer during migration")
            db.execute(
                "INSERT OR IGNORE INTO owners VALUES (?,?)", (row["bundle_id"], row["creator_id"])
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--secrets-file",
        required=True,
        help="OVH publish env.json; never pass a KEK on the command line",
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--root-key")
    parser.add_argument(
        "--expected-root", required=True, help="existing PUBLIC client trust anchor"
    )
    parser.add_argument("--aws-creators")
    parser.add_argument("--aws-owners")
    parser.add_argument("--aws-region")
    parser.add_argument(
        "--aws-plaintext", action="store_true", help="only if source rows were never KMS-wrapped"
    )
    args = parser.parse_args()
    secrets = json.loads(Path(args.secrets_file).read_text())
    database, directory, _, vault, _ = adapters(args.database, secrets["PUBLISH_KEY_KEK"])
    if args.aws_creators or args.aws_owners:
        if not args.aws_creators or not args.aws_owners or not args.aws_region:
            raise ValueError("supply both source tables and their AWS region")
        import boto3

        dynamo = boto3.resource("dynamodb", region_name=args.aws_region)
        rows = DynamoCreatorDirectory._scan(dynamo.Table(args.aws_creators))
        owners = DynamoCreatorDirectory._scan(dynamo.Table(args.aws_owners))
        roots = [r for r in rows if r.get("creator_id") == "__root__"]
        if roots and roots[0]["public_key"] != args.expected_root:
            raise ValueError("source root does not match installed client trust")
        decrypt = (
            (lambda v: v)
            if args.aws_plaintext
            else KmsEnvelopeSigner(None, boto3.client("kms", region_name=args.aws_region)).decrypt
        )
        import_rows(database, directory.cipher, rows, owners, decrypt)
    if args.root_key:
        pair = json.loads(Path(args.root_key).read_text())
        if pair["public_key"] != args.expected_root:
            raise ValueError("offline root does not match installed client trust")
        vault.store(pair["private_key"], pair["public_key"])
    if vault.public_key() != args.expected_root:
        raise ValueError("existing root key is not imported; migration is incomplete")
    print(
        "Signing identities and ownership imported without rotation. Back up the encrypted database."
    )


if __name__ == "__main__":
    main()
