"""ECS host watchdog: replace a host whose ECS agent has been gone too long.

WHY. A host that runs out of memory as a WHOLE does not OOM-kill a container; it thrashes. Its
ECS agent starves and disconnects, and with no agent nothing on the box can be stopped or
started: the task sits in STOPPING, a one-at-a-time service can never start its replacement,
and it stays down until a human reboots the host (production, 2026-09-26: the daemon, for over
half an hour). ECS itself never gives up on a host whose agent is merely silent.

WHAT IT DOES, every few minutes:
  * a host whose agent is disconnected gets a tag with the time it was first seen that way;
  * one still disconnected GRACE_SECONDS after that is TERMINATED — its Auto Scaling group
    starts a fresh one, and ECS places the service's task on it;
  * one whose agent came back loses the tag (a reboot, a brief network blip).

Stateless: the tag IS the memory between runs, so a missed or doubled run changes nothing.
The grace is long enough that a deliberate reboot never trips it. Every termination is
announced on the alerts topic, because a host being replaced is worth knowing about even
when the replacement works.
"""

from __future__ import annotations

import json
import os
import time

import boto3

CLUSTER = os.environ["CLUSTER"]
TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
GRACE_SECONDS = int(os.environ.get("GRACE_SECONDS", "600"))
TAG_KEY = "agentd:agent-lost-at"

ecs = boto3.client("ecs")
ec2 = boto3.client("ec2")
sns = boto3.client("sns")


def _hosts() -> list[dict]:
    """Every ACTIVE container instance: its EC2 id and whether its agent is connected."""
    arns: list[str] = []
    for page in ecs.get_paginator("list_container_instances").paginate(cluster=CLUSTER, status="ACTIVE"):
        arns += page["containerInstanceArns"]
    hosts: list[dict] = []
    for i in range(0, len(arns), 100):
        described = ecs.describe_container_instances(cluster=CLUSTER, containerInstances=arns[i:i + 100])
        hosts += [
            {"id": c["ec2InstanceId"], "connected": bool(c["agentConnected"]),
             "tasks": c.get("runningTasksCount", 0)}
            for c in described["containerInstances"]
        ]
    return hosts


def _lost_since(ids: list[str]) -> dict[str, float]:
    """When each host was first seen disconnected, from its tag."""
    if not ids:
        return {}
    tags = ec2.describe_tags(Filters=[
        {"Name": "resource-id", "Values": ids},
        {"Name": "key", "Values": [TAG_KEY]},
    ])["Tags"]
    return {t["ResourceId"]: float(t["Value"]) for t in tags}


def handler(event, context):
    now = time.time()
    hosts = _hosts()
    lost = _lost_since([h["id"] for h in hosts])
    result = {"hosts": len(hosts), "tagged": [], "cleared": [], "terminated": []}

    for h in hosts:
        since = lost.get(h["id"])
        if h["connected"]:
            if since is not None:
                ec2.delete_tags(Resources=[h["id"]], Tags=[{"Key": TAG_KEY}])
                result["cleared"].append(h["id"])
            continue
        if since is None:
            ec2.create_tags(Resources=[h["id"]], Tags=[{"Key": TAG_KEY, "Value": str(int(now))}])
            result["tagged"].append(h["id"])
            continue
        if now - since < GRACE_SECONDS:
            continue
        ec2.terminate_instances(InstanceIds=[h["id"]])
        result["terminated"].append(h["id"])
        if TOPIC_ARN:
            sns.publish(
                TopicArn=TOPIC_ARN,
                Subject=f"{CLUSTER}: replaced a frozen host",
                Message=(
                    f"Host {h['id']} in {CLUSTER} had no ECS agent for {int(now - since)} s "
                    f"({h['tasks']} task(s) on it), so it was terminated and its Auto Scaling group "
                    "is starting a replacement. The usual cause is the host running out of memory: "
                    "check the service memory alarms and the tasks' last logs."
                ),
            )

    print(json.dumps(result))
    return result
