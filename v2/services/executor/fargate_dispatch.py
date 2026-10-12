"""Trusted control plane. User code runs in a disposable, credential-free Fargate task.

No execution fallback is permitted when task configuration is missing. The controller
alone owns AWS credentials; tasks receive capabilities for exact S3 objects, never a role.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid


def dispatch(params: dict, s3, bucket: str, *, kind: str = "executor") -> dict:
    import boto3

    definition = os.environ.get("SANDBOX_TASK_DEFINITION", "")
    cluster = os.environ.get("SANDBOX_CLUSTER", "")
    subnets = os.environ.get("SANDBOX_SUBNETS", "").split(",")
    group = os.environ.get("SANDBOX_SECURITY_GROUP", "")
    if not all((definition, cluster, subnets[0], group, bucket)):
        raise RuntimeError("isolated runtime is not configured; execution refused")
    clean = {k: v for k, v in params.items() if k != "internal_key"}
    job = str(clean.get("job_id") or uuid.uuid4().hex)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job):
        raise ValueError("invalid job identity")
    root = f"executor/jobs/{job}/"
    persistent = clean.get("op") == "session"
    if persistent and clean.get("session_kind") not in ("browser", "mcp"):
        raise ValueError("invalid session kind")
    grants: dict[str, dict[str, str]] = {}

    def grant(key: str, *methods: str) -> None:
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
        clean["result_prefix"] = f"builder/results/{job}/"
        clean["result_stamp"] = "output"
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
    owner = str(clean.pop("tenant_owner", "") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("verified runtime owner required")
    input_key = root + "input.json"
    result_key = root + "answer.json"
    grant(result_key, "put_object")
    payload = {"kind": "session" if persistent else kind, "params": clean, "bucket": bucket, "grants": grants}
    s3.put_object(Bucket=bucket, Key=input_key, Body=json.dumps(payload).encode())
    input_url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": input_key}, ExpiresIn=1800
    )
    # Initialize the exact cancellation key atomically. Never require bucket listing
    # to distinguish an absent marker, and never overwrite an earlier cancellation.
    try:
        s3.put_object(Bucket=bucket, Key=root + "cancelled", Body=b"active", IfNoneMatch="*")
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") not in ("PreconditionFailed", "412"):
            raise
    ecs = boto3.client("ecs")
    from runtime_leases import acquire, release

    expires = int(time.time()) + (1500 if persistent else min(780, max(30, int(clean.get("timeout_s") or 120))) + 120)
    lease = acquire(owner, expires)
    try:
        launched = ecs.run_task(
        cluster=cluster, taskDefinition=definition, launchType="FARGATE",
        platformVersion="1.4.0", count=1,
        networkConfiguration={"awsvpcConfiguration": {
            "subnets": subnets, "securityGroups": [group], "assignPublicIp": "DISABLED",
        }},
        overrides={"containerOverrides": [{"name": "sandbox", "environment": [
            {"name": "SANDBOX_INPUT_URL", "value": input_url},
            {"name": "SANDBOX_OUTPUT_URL", "value": grants[result_key]["put_object"]},
        ]}]},
        tags=[{"key": "Isolation", "value": "disposable"},
              {"key": "Deadline", "value": str(expires)},
              {"key": "RuntimeLease", "value": base64.urlsafe_b64encode(json.dumps(lease, separators=(",", ":")).encode()).decode()}],
        )
    except BaseException:
        release(lease)
        raise
    tasks = launched.get("tasks", [])
    if not tasks:
        release(lease)
        raise RuntimeError("isolated task could not start")
    task = tasks[0]["taskArn"]
    s3.put_object(Bucket=bucket, Key=root + "control.json", Body=json.dumps({"task": task, "lease": lease}).encode())
    try:
        cancellation = s3.get_object(Bucket=bucket, Key=root + "cancelled")["Body"].read(32)
        if cancellation not in (b"active", b"cancelled"):
            raise RuntimeError("invalid cancellation state")
    except Exception:
        ecs.stop_task(cluster=cluster, task=task, reason="cancellation status unavailable")
        release(lease)
        raise
    if cancellation == b"cancelled":
        ecs.stop_task(cluster=cluster, task=task, reason="caller cancelled during startup")
        release(lease)
        raise RuntimeError("job cancelled during startup")
    if persistent:
        return {"ok": True, "job_id": job, "expires_at": expires,
                "status_url": s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": result_key}, ExpiresIn=1800),
                "workspace_put_url": grants[root + "workspace.zip"]["put_object"],
                "rpc_put_urls": [grants[root + f"rpc-in-{n}.json"]["put_object"] for n in range(256)],
                "rpc_get_urls": [grants[root + f"rpc-out-{n}.json"]["get_object"] for n in range(256)]}
    deadline = time.monotonic() + min(780, max(30, float(clean.get("timeout_s") or 120))) + 90
    try:
        while time.monotonic() < deadline:
            try:
                body = s3.get_object(Bucket=bucket, Key=result_key)["Body"].read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    raise ValueError("runtime response too large")
                return json.loads(body)
            except Exception as exc:
                # An exact-key GET is 403 when absent and ListBucket is deliberately
                # unavailable. It carries no data; bounded polling remains fail closed.
                if getattr(exc, "response", {}).get("Error", {}).get("Code") not in ("NoSuchKey", "404", "403", "AccessDenied"):
                    raise
            state = ecs.describe_tasks(cluster=cluster, tasks=[task]).get("tasks", [])
            if state and state[0].get("lastStatus") == "STOPPED":
                # A final S3 write is strongly consistent, so a stopped task without
                # a result cannot become successful on a later poll.
                try:
                    return json.loads(s3.get_object(Bucket=bucket, Key=result_key)["Body"].read())
                except Exception as exc:
                    if getattr(exc, "response", {}).get("Error", {}).get("Code") not in ("NoSuchKey", "404", "403", "AccessDenied"):
                        raise
                    raise RuntimeError("isolated task stopped without a result") from None
            time.sleep(1)
        raise TimeoutError("isolated task exceeded its deadline")
    finally:
        try:
            ecs.stop_task(cluster=cluster, task=task, reason="job finished or timed out")
        finally:
            release(lease)


def cancel(params: dict, s3, bucket: str) -> dict:
    import boto3

    job = str(params.get("job_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job):
        raise ValueError("invalid job identity")
    s3.put_object(Bucket=bucket, Key=f"executor/jobs/{job}/cancelled", Body=b"cancelled")
    try:
        control = json.loads(s3.get_object(Bucket=bucket, Key=f"executor/jobs/{job}/control.json")["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {"ok": True}
    boto3.client("ecs").stop_task(cluster=os.environ["SANDBOX_CLUSTER"], task=control["task"], reason="caller cancelled")
    from runtime_leases import release

    release(control.get("lease", {}))
    return {"ok": True}
