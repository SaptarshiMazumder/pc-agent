"""One configured S3 client for AWS or an S3-compatible store such as R2."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


def s3_client():
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("OBJECT_STORAGE_ENDPOINT", "").strip()
    region = os.environ.get("OBJECT_STORAGE_REGION", "").strip()
    if not region:
        region = "auto" if endpoint else os.environ.get("AWS_REGION", "") or None
    kwargs = {
        "region_name": region,
        "config": Config(
            signature_version="s3v4", s3={"addressing_style": "path" if endpoint else "virtual"}
        ),
    }
    if endpoint:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(
                "OBJECT_STORAGE_ENDPOINT must be an HTTPS endpoint without credentials"
            )
        kwargs["endpoint_url"] = endpoint
        for name, argument in (
            ("OBJECT_STORAGE_ACCESS_KEY_ID", "aws_access_key_id"),
            ("OBJECT_STORAGE_SECRET_ACCESS_KEY", "aws_secret_access_key"),
        ):
            value = os.environ.get(name, "").strip()
            if not value:
                raise RuntimeError(f"{name} is required for external object storage")
            kwargs[argument] = value
    elif region:
        kwargs["endpoint_url"] = f"https://s3.{region}.amazonaws.com"
    return boto3.client("s3", **kwargs)
