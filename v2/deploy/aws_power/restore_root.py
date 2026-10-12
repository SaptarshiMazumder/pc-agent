"""Restore the original pinned signing key; never replace it with a new identity."""
import base64
import json
import os

import boto3
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PIN = "Fkez25JIQDUtvwvyghwGdY2Mw//fDG7ITZCmB7CG7Nc="


def main():
    raw = os.environ.get("REGISTRY_KEYPAIR", "")
    if not raw:
        raise RuntimeError("The protected registry signing key is unavailable")
    pair = json.loads(raw)
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(pair["private_key"]))
    derived = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    if pair["public_key"] != PIN or derived != PIN:
        raise RuntimeError("The repository key is a creator key, not the pinned platform root; original root recovery is still required")
    session = boto3.Session(region_name="ap-south-1")
    if session.client("sts").get_caller_identity()["Account"] != "891612568944":
        raise RuntimeError("Wrong AWS account")
    table = session.resource("dynamodb").Table("agentd-production-publish-creators")
    existing = table.get_item(Key={"account_id": "__root__"}, ConsistentRead=True).get("Item")
    if existing:
        if existing.get("public_key") != PIN:
            raise RuntimeError("The vault contains a different signing identity")
        print("The original registry root is already restored")
        return
    ciphertext = session.client("kms").encrypt(
        KeyId="d3c6bef3-fe80-4e43-a93d-030f308279d5", Plaintext=pair["private_key"].encode())["CiphertextBlob"]
    table.put_item(Item={"account_id": "__root__", "creator_id": "__root__", "name": "platform root key",
                         "state": "root", "public_key": PIN,
                         "private_key": base64.b64encode(ciphertext).decode()},
                   ConditionExpression="attribute_not_exists(account_id)")
    print("Original registry root restored, encrypted by the existing production KMS key")


if __name__ == "__main__":
    main()
