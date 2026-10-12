"""Trusted, atomic admission limits. Worker tasks cannot access this table."""
from __future__ import annotations

import os
import time
import uuid


def acquire(owner: str, deadline: int) -> dict:
    import boto3
    from botocore.exceptions import ClientError

    table = boto3.resource("dynamodb").Table(os.environ["SANDBOX_LEASE_TABLE"])
    token = uuid.uuid4().hex
    held = []
    try:
        for namespace, limit in (("global", 64), ("tenant:" + owner, 8)):
            for slot in range(limit):
                key = f"{namespace}:{slot}"
                try:
                    table.put_item(Item={"pk": key, "token": token, "expires": deadline + 180},
                                   ConditionExpression="attribute_not_exists(pk) OR expires < :now",
                                   ExpressionAttributeValues={":now": int(time.time())})
                    held.append(key)
                    break
                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                        raise
            else:
                raise RuntimeError("isolated runtime capacity reached; retry after an active job finishes")
        return {"keys": held, "token": token}
    except BaseException:
        release({"keys": held, "token": token})
        raise


def release(lease: dict) -> None:
    import boto3
    from botocore.exceptions import ClientError

    table = boto3.resource("dynamodb").Table(os.environ["SANDBOX_LEASE_TABLE"])
    for key in lease.get("keys", []):
        try:
            table.delete_item(Key={"pk": key}, ConditionExpression="#token = :token",
                              ExpressionAttributeNames={"#token": "token"},
                              ExpressionAttributeValues={":token": lease["token"]})
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
