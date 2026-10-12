"""Enforce deadlines outside untrusted tasks, including crashed controllers."""
import base64
import datetime
import json
import os
import time

import boto3
from runtime_leases import release


def handler(event, context):
    ecs = boto3.client("ecs")
    cluster = os.environ["SANDBOX_CLUSTER"]
    prefix = os.environ["SANDBOX_FAMILY_PREFIX"]
    stopped = 0
    for kind in ("executor", "builder"):
        paginator = ecs.get_paginator("list_tasks")
        for page in paginator.paginate(cluster=cluster, family=prefix + kind, desiredStatus="RUNNING"):
            arns = page.get("taskArns", [])
            if not arns:
                continue
            for task in ecs.describe_tasks(cluster=cluster, tasks=arns, include=["TAGS"]).get("tasks", []):
                definition = task["taskDefinitionArn"].split("/")[-1].rsplit(":", 1)[0]
                if definition != prefix + kind:
                    continue
                tags = {tag["key"]: tag["value"] for tag in task.get("tags", [])}
                created = task.get("createdAt") or datetime.datetime.now(datetime.timezone.utc)
                deadline = min(float(tags.get("Deadline") or created.timestamp() + 900), created.timestamp() + 1620)
                if time.time() < deadline:
                    continue
                ecs.stop_task(cluster=cluster, task=task["taskArn"], reason="external isolation deadline")
                if tags.get("RuntimeLease"):
                    release(json.loads(base64.urlsafe_b64decode(tags["RuntimeLease"])))
                stopped += 1
    return {"stopped": stopped}
