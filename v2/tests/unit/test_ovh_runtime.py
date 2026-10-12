"""OVH security/admission lifecycle contracts; Linux host acceptance is separate."""

import io
import json
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.common import docker_dispatch, runtime_dispatch
from services.common.http_adapter import create_app as http_app
from services.executor import fargate_runner
from services.runtime_controller.app import Controller, SecretStore, create_app


class NotFound(Exception):
    status_code = 404


class Resources:
    def __init__(self, network=False):
        self.items, self.network = {}, network
        self.fail_start = False

    def create(self, *args, **kwargs):
        name = args[0] if self.network else kwargs["name"]
        labels = kwargs.get("labels", {})
        obj = SimpleNamespace(
            name=name,
            status="created",
            labels=labels,
            attrs={"Labels": labels},
            options=kwargs,
            connections=[],
        )
        obj.connect = lambda c, **kw: obj.connections.append((c, kw))

        def start():
            if self.fail_start and name.startswith("adjob-"):
                raise RuntimeError("test engine failure")
            obj.status = "running"

        obj.start = start
        obj.remove = lambda **kw: self.items.pop(name, None)
        self.items[name] = obj
        return obj

    def get(self, name):
        if name not in self.items:
            raise NotFound()
        return self.items[name]

    def list(self, **kwargs):
        return list(self.items.values())


@pytest.fixture
def controller(tmp_path):
    engine = SimpleNamespace(
        containers=Resources(), networks=Resources(True), info=lambda: {"Runtimes": {"runsc": {}}}
    )
    return Controller(
        engine,
        tmp_path / "runtime.db",
        {"executor": "executor:tested", "builder": "builder:tested"},
        "public-egress-net",
        "objects.test",
    )


def job(name="job-1", **changes):
    return {
        "job_id": name,
        "owner": "a" * 64,
        "expires": int(time.time()) + 300,
        "kind": "executor",
        "input_url": "https://objects.test/input?signature=secret",
        "output_url": "https://objects.test/output?signature=secret",
        **changes,
    }


def test_missing_gvisor_refuses_execution(tmp_path):
    with pytest.raises(RuntimeError, match="runsc"):
        Controller(
            SimpleNamespace(info=lambda: {"Runtimes": {"runc": {}}}),
            tmp_path / "x",
            {},
            "egress",
            "objects.test",
        )


def test_job_has_no_host_mount_secrets_or_trusted_network(controller, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "must-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
    controller.start(job())
    worker = controller.engine.containers.get(controller.names("job-1")[0])
    options = worker.options
    assert options["runtime"] == "runsc" and options["read_only"]
    assert options["user"] == "1000:1000" and options["cap_drop"] == ["ALL"]
    assert options["mem_limit"] == "2g" and options["pids_limit"] == 256
    assert "volumes" not in options and "privileged" not in options
    assert options["network"] == controller.names("job-1")[2]
    assert "must-not-leak" not in json.dumps(options["environment"])
    assert options["environment"]["HTTPS_PROXY"] == "http://public-egress:8080"
    net = controller.engine.networks.get(options["network"])
    assert net.options["internal"]
    assert len(net.connections) == 1


def test_concurrent_admission_cannot_exceed_one_slot(controller):
    def attempt(n):
        try:
            return controller.start(job(f"job-{n}"))["ok"]
        except OverflowError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(8))) == 1


def test_other_owner_cannot_inspect_or_cancel(controller):
    controller.start(job())
    for method in (controller.status, controller.cancel):
        with pytest.raises(PermissionError):
            method(job(owner="b" * 64))
    assert controller.status(job())["running"]


def test_cancel_before_start_never_resurrects(controller):
    controller.cancel(job())
    with pytest.raises(ValueError, match="already used"):
        controller.start(job())
    assert not controller.engine.containers.items


def test_start_failure_and_deadline_reap_release_capacity(controller):
    controller.engine.containers.fail_start = True
    with pytest.raises(RuntimeError):
        controller.start(job())
    assert not controller.engine.containers.items and not controller.engine.networks.items
    controller.engine.containers.fail_start = False
    controller.start(job("next"))
    with controller.db() as db:
        db.execute("UPDATE jobs SET expires=0")
    controller.reap()
    assert not controller.status(job("next"))["running"]
    controller.start(job("after-reap"))


def test_finished_session_releases_slot_without_waiting_full_deadline(controller):
    controller.start(job())
    controller.engine.containers.get(controller.names("job-1")[0]).status = "exited"
    controller.start(job("next"))
    assert len(controller.engine.containers.items) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "caller-image"},
        {"expires": int(time.time()) + 99999},
        {"input_url": "http://169.254.169.254/metadata"},
        {"output_url": "https://attacker.test/upload"},
    ],
)
def test_caller_cannot_select_image_deadline_or_capability_host(controller, change):
    with pytest.raises(ValueError):
        controller.start(job(**change))
    assert not controller.engine.containers.items


def test_private_api_authentication_and_busy_response(controller, monkeypatch):
    monkeypatch.setenv("RUNTIME_CONTROL_KEY", "r" * 48)
    monkeypatch.delenv("PLATFORM_SECRETS_ROOT", raising=False)
    with TestClient(create_app(controller)) as client:
        assert client.post("/jobs/start", json=job()).status_code == 401
        headers = {"X-Control-Key": "r" * 48}
        assert client.post("/jobs/start", json=job(), headers=headers).status_code == 200
        assert client.post("/jobs/start", json=job("next"), headers=headers).status_code == 429
        assert (
            client.post("/jobs/cancel", json=job(owner="b" * 64), headers=headers).status_code
            == 403
        )


def test_configured_runtime_never_falls_back(monkeypatch):
    monkeypatch.setenv("ISOLATED_RUNTIME", "typo")
    with pytest.raises(RuntimeError, match="refused"):
        runtime_dispatch.dispatch({}, None, "bucket")
    monkeypatch.setenv("ISOLATED_RUNTIME", "docker")
    monkeypatch.delenv("RUNTIME_CONTROL_URL", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        runtime_dispatch.dispatch({}, None, "bucket")


def test_capability_payload_contains_no_control_key_and_cleanup_is_bounded(monkeypatch):
    monkeypatch.setenv("RUNTIME_CONTROL_URL", "http://controller")
    monkeypatch.setenv("RUNTIME_CONTROL_KEY", "must-not-leak")
    objects = {}
    calls = []

    class S3:
        def generate_presigned_url(self, method, Params, **kw):
            return "https://objects.test/" + Params["Key"] + "?method=" + method

        def put_object(self, Bucket, Key, Body, **kw):
            objects[Key] = Body

        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(objects[Key])}

    def control(path, data):
        calls.append((path, data))
        if path == "/jobs/start":
            objects["executor/jobs/test/answer.json"] = b'{"ok": true}'
        return {"ok": True}

    monkeypatch.setattr(docker_dispatch, "control", control)
    result = docker_dispatch.dispatch(
        {"job_id": "test", "tenant_owner": "a" * 64, "internal_key": "must-not-leak", "op": "run"},
        S3(),
        "bucket",
    )
    assert result == {"ok": True}
    payload = objects["executor/jobs/test/input.json"]
    assert b"must-not-leak" not in payload and b"tenant_owner" not in payload
    grants = json.loads(payload)["grants"]
    assert all(key.startswith("executor/jobs/test/") for key in grants)
    assert calls[-1][0] == "/jobs/cancel"


def test_http_adapter_rejects_oversize_before_calling_handler(monkeypatch):
    monkeypatch.setenv("HTTP_MAX_BODY_BYTES", "4")
    calls = []
    module = SimpleNamespace(handler=lambda *args: calls.append(args))
    with TestClient(http_app(module)) as client:
        assert client.post("/executor", content=b"12345").status_code == 413
    assert calls == []


def test_secret_metadata_never_exposes_values_and_encryption_keys_cannot_rotate(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "services.runtime_controller.app.os.chown", lambda *args: None, raising=False
    )
    (tmp_path / "model-proxy").mkdir()
    (tmp_path / "consumers.json").write_text(json.dumps({"GEMINI_API_KEY": ["model-proxy"]}))
    path = tmp_path / "model-proxy" / "env.json"
    path.write_text(
        json.dumps({"GEMINI_API_KEY": "private-value", "AGENTD_IDENTITY_KEK": "do-not-change"})
    )
    store = SecretStore(None, tmp_path)
    assert "private-value" not in json.dumps(store.metadata())
    assert store.update("GEMINI_API_KEY", "replacement") == ["model-proxy"]
    assert json.loads(path.read_text()) == {
        "GEMINI_API_KEY": "replacement",
        "AGENTD_IDENTITY_KEK": "do-not-change",
    }
    with pytest.raises(ValueError):
        store.update("AGENTD_IDENTITY_KEK", "break-existing-data")


@pytest.mark.parametrize("failure", ["not-ready", "unauthorized"])
def test_worker_waits_for_proxy_readiness_but_never_retries_authorization_failure(
    monkeypatch, failure
):
    import sys

    monkeypatch.setenv("SANDBOX_INPUT_URL", "https://objects.test/input")
    monkeypatch.setenv("SANDBOX_OUTPUT_URL", "https://objects.test/output")
    monkeypatch.setitem(
        sys.modules, "handler", SimpleNamespace(_OPS={"run": lambda params: {"ok": True}})
    )
    calls = []
    payload = {"kind": "executor", "bucket": "scratch", "grants": {}, "params": {"op": "run"}}

    def request(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            if failure == "unauthorized":
                raise urllib.error.HTTPError("https://objects.test/input", 403, "denied", {}, None)
            raise urllib.error.URLError("proxy is still starting")
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(fargate_runner.urllib.request, "urlopen", request)
    monkeypatch.setattr(fargate_runner.time, "sleep", lambda delay: None)
    if failure == "unauthorized":
        with pytest.raises(urllib.error.HTTPError):
            fargate_runner.main()
        assert len(calls) == 1
    else:
        fargate_runner.main()
        assert len(calls) == 3
        assert json.loads(calls[-1].data) == {"ok": True}
