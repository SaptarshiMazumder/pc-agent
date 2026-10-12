"""Capabilities, controller separation, cancellation and external task deadlines."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import datetime

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_nested_browser_sandbox_is_disabled_only_inside_disposable_vm(monkeypatch):
    from agent_runtime.infrastructure.tools.browser.isolated_launch import options
    from agent_runtime.infrastructure.tools.browser.providers.playwright import stealth_chromium_kwargs
    config = SimpleNamespace(plugins={})
    monkeypatch.delenv("AGENTD_ISOLATED_TASK", raising=False)
    assert options() == {}
    assert stealth_chromium_kwargs(config, headless=True, persistent=False)["chromium_sandbox"] is True
    monkeypatch.setenv("AGENTD_ISOLATED_TASK", "1")
    monkeypatch.setenv("HTTP_PROXY", "http://public-only-proxy:8080")
    assert options()["chromium_sandbox"] is False
    assert stealth_chromium_kwargs(config, headless=True, persistent=False)["chromium_sandbox"] is False
    assert options()["proxy"] == {"server": "http://public-only-proxy:8080"}


def source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_object_capabilities_cannot_read_another_job_or_bucket():
    runner = source("isolated_runner_test", ROOT / "services/executor/fargate_runner.py")
    adapter = runner.ScopedS3("scratch", {"executor/jobs/own/workspace.zip": {"get_object": "own-read"}})
    assert adapter.url("scratch", "executor/jobs/own/workspace.zip", "get_object") == "own-read"
    for bucket, key, method in (("scratch", "executor/jobs/victim/workspace.zip", "get_object"),
                               ("other-bucket", "executor/jobs/own/workspace.zip", "get_object"),
                               ("scratch", "executor/jobs/own/workspace.zip", "put_object"),
                               ("scratch", "executor/jobs/own/control.json", "get_object")):
        with pytest.raises(PermissionError):
            adapter.url(bucket, key, method)


class Missing(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class Objects:
    exceptions = SimpleNamespace(NoSuchKey=Missing)

    def __init__(self):
        self.objects = {}

    def generate_presigned_url(self, method, Params, **_):
        return method + ":" + Params["Key"]

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None):
        if IfNoneMatch and Key in self.objects:
            exc = Exception("conditional write collision")
            exc.response = {"Error": {"Code": "PreconditionFailed"}}
            raise exc
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        return {}


@pytest.fixture
def controller(monkeypatch):
    import boto3

    module = source("dispatch_test", ROOT / "services/executor/fargate_dispatch.py")
    objects = Objects()
    launches, stops, released = [], [], []
    ecs = SimpleNamespace(run_task=lambda **kw: launches.append(kw) or {"tasks": [{"taskArn": "own-task"}]},
                          stop_task=lambda **kw: stops.append(kw))
    monkeypatch.setattr(boto3, "client", lambda _: ecs)
    monkeypatch.setitem(sys.modules, "runtime_leases", SimpleNamespace(
        acquire=lambda owner, deadline: {"keys": ["private-lease"], "token": "lease-token"},
        release=lambda lease: released.append(lease)))
    for name, value in {"SANDBOX_TASK_DEFINITION": "isolated-task-definition", "SANDBOX_CLUSTER": "own-cluster",
                        "SANDBOX_SUBNETS": "private-a,private-b", "SANDBOX_SECURITY_GROUP": "isolated-sg"}.items():
        monkeypatch.setenv(name, value)
    return module, objects, launches, stops, released


def test_persistent_runtime_receives_no_controller_keys_or_other_tenant_objects(controller):
    module, objects, launches, _, _ = controller
    result = module.dispatch({"op": "session", "session_kind": "browser", "job_id": "own",
                              "internal_key": "platform-secret", "tenant_owner": "a" * 64}, objects, "scratch")
    assert result["ok"]
    launch = launches[0]
    assert launch["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] == "DISABLED"
    assert {e["name"] for e in launch["overrides"]["containerOverrides"][0]["environment"]} == {"SANDBOX_INPUT_URL", "SANDBOX_OUTPUT_URL"}
    wire = objects.objects["executor/jobs/own/input.json"]
    assert b"platform-secret" not in wire
    assert b"tenant_owner" not in wire
    grants = json.loads(wire)["grants"]
    assert "executor/jobs/own/control.json" not in grants
    assert "executor/jobs/own/cancelled" not in grants
    assert all(key.startswith("executor/jobs/own/") for key in grants)
    import re
    assert dict((t["key"], t["value"]) for t in launch["tags"])["Deadline"]
    assert all(len(t["value"]) <= 256 and re.fullmatch(r"[A-Za-z0-9 _./=+\-:@]+", t["value"]) for t in launch["tags"])
    lease = next(t["value"] for t in launch["tags"] if t["key"] == "RuntimeLease")
    assert json.loads(base64.urlsafe_b64decode(lease))["token"] == "lease-token"


@pytest.mark.parametrize("key", ["executor/jobs/victim/workspace.zip", "../../victim.zip", "executor/code/../victim.zip"])
def test_foreign_workspace_references_are_rejected_before_launch(controller, key):
    module, objects, launches, _, _ = controller
    with pytest.raises(ValueError):
        module.dispatch({"op": "run", "job_id": "own", "workspace_key": key, "tenant_owner": "a" * 64}, objects, "scratch")
    assert launches == []


def test_missing_isolation_configuration_never_executes_locally(controller, monkeypatch):
    module, objects, launches, _, _ = controller
    monkeypatch.delenv("SANDBOX_TASK_DEFINITION")
    with pytest.raises(RuntimeError, match="execution refused"):
        module.dispatch({"op": "run"}, objects, "scratch")
    assert launches == []


def test_cancel_during_startup_stops_the_task_and_releases_capacity(controller):
    module, objects, launches, stops, released = controller
    module.cancel({"job_id": "own"}, objects, "scratch")
    with pytest.raises(RuntimeError, match="cancelled during startup"):
        module.dispatch({"op": "session", "session_kind": "browser", "job_id": "own", "tenant_owner": "a" * 64}, objects, "scratch")
    assert len(launches) == len(stops) == len(released) == 1


def test_external_reaper_stops_expired_runtime_and_never_a_service_or_other_stack(monkeypatch):
    import boto3

    released, stops = [], []
    monkeypatch.setitem(sys.modules, "runtime_leases", SimpleNamespace(release=lambda lease: released.append(lease)))
    module = source("reaper_test", ROOT / "infra/modules/lambda/sandbox_reaper.py")
    now = time.time()
    def task(arn, family, deadline):
        return {"taskArn": arn, "taskDefinitionArn": "arn:aws:ecs:region:account:task-definition/" + family + ":1",
                "createdAt": datetime.datetime.fromtimestamp(now - 200, datetime.timezone.utc),
                "tags": [{"key": "Deadline", "value": str(deadline)},
                         {"key": "RuntimeLease", "value": base64.urlsafe_b64encode(b'{"keys":["own"],"token":"private"}').decode()}]}
    tasks = [task("expired", "own-sandbox-executor", now - 1),
             task("live", "own-sandbox-executor", now + 100),
             task("service", "own-daemon", now - 1), task("other", "other-sandbox-executor", now - 1)]
    paginator = SimpleNamespace(paginate=lambda **kw: [{"taskArns": ["expired", "live", "service", "other"]}] if kw["family"].endswith("executor") else [])
    ecs = SimpleNamespace(get_paginator=lambda _: paginator, describe_tasks=lambda **kw: {"tasks": tasks},
                          stop_task=lambda **kw: stops.append(kw))
    monkeypatch.setattr(boto3, "client", lambda _: ecs)
    monkeypatch.setenv("SANDBOX_CLUSTER", "own-cluster")
    monkeypatch.setenv("SANDBOX_FAMILY_PREFIX", "own-sandbox-")
    assert module.handler({}, None) == {"stopped": 1}
    assert [item["task"] for item in stops] == ["expired"]
    assert len(released) == 1


def test_controller_polls_missing_exact_keys_without_bucket_listing(controller, monkeypatch):
    import boto3
    module, objects, launches, _, released = controller
    calls = []
    original = objects.get_object
    def get_object(**kwargs):
        if kwargs["Key"].endswith("answer.json"):
            calls.append(1)
            if len(calls) == 1:
                error = Exception("missing exact object without ListBucket")
                error.response = {"Error": {"Code": "AccessDenied"}}
                raise error
            return {"Body": io.BytesIO(b'{"ok":true,"output":"own result"}')}
        return original(**kwargs)
    monkeypatch.setattr(objects, "get_object", get_object)
    ecs = boto3.client("ecs")
    ecs.describe_tasks = lambda **_: {"tasks": [{"lastStatus": "RUNNING"}]}
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    result = module.dispatch({"op": "shell", "job_id": "own", "tenant_owner": "a" * 64}, objects, "scratch")
    assert result == {"ok": True, "output": "own result"}
    assert len(calls) == 2 and len(released) == 1
