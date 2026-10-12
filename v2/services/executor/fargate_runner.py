"""Disposable task entrypoint. This image contains no platform credentials or tenant mount."""
from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


class ScopedS3:
    """S3-shaped adapter with exact-object capabilities instead of AWS credentials."""

    def __init__(self, bucket: str, grants: dict):
        self.bucket, self.grants = bucket, grants

    def url(self, bucket: str, key: str, method: str) -> str:
        if bucket != self.bucket or method not in self.grants.get(key, {}):
            raise PermissionError("object capability denied")
        return self.grants[key][method]

    def generate_presigned_url(self, method, Params, **_):  # noqa: N803
        return self.url(Params["Bucket"], Params["Key"], method)

    def get_object(self, *, Bucket, Key):  # noqa: N803
        with urllib.request.urlopen(self.url(Bucket, Key, "get_object"), timeout=30) as response:
            data = response.read(300 * 1024 * 1024 + 1)
        if len(data) > 300 * 1024 * 1024:
            raise ValueError("object exceeds runtime transfer limit")
        return {"Body": io.BytesIO(data)}

    def put_object(self, *, Bucket, Key, Body, **_):  # noqa: N803
        body = Body.read() if hasattr(Body, "read") else Body
        request = urllib.request.Request(self.url(Bucket, Key, "put_object"), data=body, method="PUT")
        with urllib.request.urlopen(request, timeout=60):
            pass

    def download_file(self, bucket, key, destination):
        Path(destination).write_bytes(self.get_object(Bucket=bucket, Key=key)["Body"].read())

    def upload_file(self, source, bucket, key):
        self.put_object(Bucket=bucket, Key=key, Body=Path(source).read_bytes())


def main() -> None:
    input_url = os.environ.pop("SANDBOX_INPUT_URL")
    output_url = os.environ.pop("SANDBOX_OUTPUT_URL")
    # A new per-job egress proxy may still be binding when the worker starts.
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen(input_url, timeout=10) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError:
            raise  # An authorization refusal must never become a local fallback.
        except (urllib.error.URLError, OSError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
    # All remaining environment is public runtime configuration. No controller key,
    # role credentials or caller JWT is ever handed to this task.
    import handler

    adapter = ScopedS3(payload["bucket"], payload["grants"])
    handler._s3 = lambda: adapter
    handler.SCRATCH_BUCKET = payload["bucket"]
    try:
        if payload["kind"] == "session":
            import asyncio

            from session_runner import serve

            asyncio.run(serve(payload, adapter))
            result = {"ok": True}
        elif payload["kind"] == "builder":
            result = handler.run_job(payload["params"])
        else:
            result = handler._OPS[payload["params"].get("op", "run")](payload["params"])
    except BaseException as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    request = urllib.request.Request(output_url, data=json.dumps(result).encode(), method="PUT")
    with urllib.request.urlopen(request, timeout=60):
        pass


if __name__ == "__main__":
    main()
