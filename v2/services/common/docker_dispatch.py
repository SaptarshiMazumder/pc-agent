"""S3 capability transport with a private, authenticated gVisor controller."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
import uuid

log = logging.getLogger(__name__)


def control(path, data):
    base = os.environ.get("RUNTIME_CONTROL_URL", "").rstrip("/")
    key = os.environ.get("RUNTIME_CONTROL_KEY", "")
    if not base or not key:
        raise RuntimeError("isolated controller is not configured; execution refused")
    request = urllib.request.Request(
        base + path,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json", "X-Control-Key": key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def job_id(value):
    value = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("invalid job identity")
    return value


def missing(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code") in (
        "NoSuchKey",
        "404",
        "403",
        "AccessDenied",
    )


def dispatch(params, s3, bucket, *, kind="executor"):
    if not bucket or kind not in ("executor", "builder"):
        raise ValueError("invalid isolated runtime configuration")
    # Check configuration BEFORE creating object capabilities.
    if not os.environ.get("RUNTIME_CONTROL_URL") or not os.environ.get("RUNTIME_CONTROL_KEY"):
        raise RuntimeError("isolated controller is not configured; execution refused")
    clean = {k: v for k, v in params.items() if k != "internal_key"}
    owner = str(clean.pop("tenant_owner", "") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("verified runtime owner required")
    job = job_id(clean.get("job_id") or uuid.uuid4().hex)
    root = f"executor/jobs/{job}/"
    persistent = clean.get("op") == "session"
    if persistent and (kind != "executor" or clean.get("session_kind") not in ("browser", "mcp")):
        raise ValueError("invalid persistent session")
    grants = {}

    def grant(key, *methods):
        entry = grants.setdefault(key, {})
        for method in methods:
            entry[method] = s3.generate_presigned_url(
                method, Params={"Bucket": bucket, "Key": key}, ExpiresIn=1800
            )

    if kind == "builder":
        source = str(clean.get("sources_key") or "")
        if not re.fullmatch(r"builder/sources/[A-Za-z0-9_.-]+\.zip", source):
            raise ValueError("invalid build source")
        grant(source, "get_object")
        clean.update(result_prefix=f"builder/results/{job}/", result_stamp="output")
        for suffix in ("log", "zip"):
            grant(f"builder/results/{job}/output.{suffix}", "put_object", "get_object")
    else:
        code = str(clean.get("code_key") or "")
        if code:
            if not re.fullmatch(r"executor/code/[A-Za-z0-9_-]+\.zip", code):
                raise ValueError("invalid code reference")
            grant(code, "get_object")
        workspace = str(clean.get("workspace_key") or "")
        if workspace:
            if workspace != root + "workspace.zip":
                raise ValueError("workspace does not belong to this job")
            grant(workspace, "get_object")
        grant(root + "changes.zip", "put_object", "get_object")
        for slot in range(256):
            grant(root + f"req-{slot}.json", "put_object")
            grant(root + f"res-{slot}.json", "get_object")
        if persistent:
            grant(root + "workspace.zip", "get_object", "put_object")
            for slot in range(256):
                grant(root + f"rpc-in-{slot}.json", "get_object", "put_object")
                grant(root + f"rpc-out-{slot}.json", "get_object", "put_object")
    clean["job_id"] = job
    answer = root + "answer.json"
    grant(answer, "put_object")
    payload = {
        "kind": "session" if persistent else kind,
        "params": clean,
        "bucket": bucket,
        "grants": grants,
    }
    input_key = root + "input.json"
    s3.put_object(Bucket=bucket, Key=input_key, Body=json.dumps(payload).encode())
    try:
        s3.put_object(Bucket=bucket, Key=root + "cancelled", Body=b"active", IfNoneMatch="*")
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") not in (
            "412",
            "PreconditionFailed",
        ):
            raise
    lifetime = 1500 if persistent else min(780, max(30, int(clean.get("timeout_s") or 120))) + 120
    expires = int(time.time()) + lifetime
    control(
        "/jobs/start",
        {
            "job_id": job,
            "owner": owner,
            "kind": kind,
            "expires": expires,
            "input_url": s3.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": input_key}, ExpiresIn=1800
            ),
            "output_url": grants[answer]["put_object"],
        },
    )
    try:
        marker = s3.get_object(Bucket=bucket, Key=root + "cancelled")["Body"].read(32)
        if marker != b"active":
            raise RuntimeError("job cancelled during startup")
        if persistent:
            return {
                "ok": True,
                "job_id": job,
                "expires_at": expires,
                "status_url": s3.generate_presigned_url(
                    "get_object", Params={"Bucket": bucket, "Key": answer}, ExpiresIn=1800
                ),
                "workspace_put_url": grants[root + "workspace.zip"]["put_object"],
                "rpc_put_urls": [
                    grants[root + f"rpc-in-{n}.json"]["put_object"] for n in range(256)
                ],
                "rpc_get_urls": [
                    grants[root + f"rpc-out-{n}.json"]["get_object"] for n in range(256)
                ],
            }
        deadline = time.monotonic() + lifetime - 30
        while time.monotonic() < deadline:
            try:
                body = s3.get_object(Bucket=bucket, Key=answer)["Body"].read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    raise ValueError("runtime response too large")
                return json.loads(body)
            except Exception as exc:
                if not missing(exc):
                    raise
            status = control("/jobs/status", {"job_id": job, "owner": owner})
            if not status.get("running"):
                # Retry the final strongly-consistent result before reporting worker failure.
                try:
                    body = s3.get_object(Bucket=bucket, Key=answer)["Body"].read(
                        2 * 1024 * 1024 + 1
                    )
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError("runtime response too large")
                    return json.loads(body)
                except Exception as exc:
                    if not missing(exc):
                        raise
                    raise RuntimeError("isolated job stopped without a result") from None
            time.sleep(1)
        raise TimeoutError("isolated job exceeded its deadline")
    except BaseException:
        try:
            control("/jobs/cancel", {"job_id": job, "owner": owner})
        except Exception:
            log.error("sandbox cleanup unavailable; external deadline reaper remains required")
        raise
    finally:
        if not persistent:
            try:
                control("/jobs/cancel", {"job_id": job, "owner": owner})
            except Exception:
                log.error("sandbox cleanup unavailable; external deadline reaper remains required")


def cancel(params, s3, bucket):
    job = job_id(params.get("job_id"))
    owner = str(params.get("tenant_owner") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("verified runtime owner required")
    result = control("/jobs/cancel", {"job_id": job, "owner": owner})
    s3.put_object(Bucket=bucket, Key=f"executor/jobs/{job}/cancelled", Body=b"cancelled")
    return result
