import asyncio
import io
import json
import shlex
import struct
import time
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import comfy_bridge
import gpu_model_download_worker
from agent_runtime.infrastructure.net.outbound import Response
from gpu_model_download_client import GpuModelDownloadClient
from gpu_model_download_worker import GpuModelDownloadWorker
from model_download_redirect_policy import ModelDownloadRedirectPolicy
from model_download_request import ModelDownloadRequest
from model_installation_service import ModelInstallationService
from workflow_model_manifest import WorkflowModelManifest
from workflow_reference_repository import WorkflowReferenceRepository


def request(filename="qwen_image_edit_2511_fp8mixed.safetensors", kind="diffusion_model"):
    return ModelDownloadRequest(filename, f"https://huggingface.co/Comfy-Org/Qwen/resolve/main/{filename}", kind)


def response(data, status=200):
    return Response(status=status, text=json.dumps(data))


@pytest.mark.parametrize("url", [
    "http://huggingface.co/org/repo/resolve/main/model.safetensors",
    "https://evil.test/org/repo/resolve/main/model.safetensors",
    "https://huggingface.co@127.0.0.1/org/repo/resolve/main/model.safetensors",
    "https://huggingface.co/org/repo/blob/main/model.safetensors",
    "https://huggingface.co/org/repo/resolve/main/%2e%2e/model.safetensors",
    "https://huggingface.co/org/repo/resolve/main/model.safetensors?token=secret",
    "https://huggingface.co/org/repo/resolve/main/different.safetensors",
])
def test_direct_download_refuses_untrusted_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        ModelDownloadRequest("model.safetensors", url, "vae")


@pytest.mark.parametrize("filename", ["../model.safetensors", "x;bad.safetensors", "model.ckpt", "x\\model.safetensors"])
def test_direct_download_refuses_code_and_path_traversal(filename):
    with pytest.raises(ValueError):
        request(filename)


def test_destination_dedupe_survives_url_revision_change():
    first = request()
    second = ModelDownloadRequest(first.filename, first.url.replace("/main/", "/v2/"), first.kind)
    assert first.job_id == second.job_id
    assert first.source_id != second.source_id


def test_redirect_cannot_reach_private_network():
    with pytest.raises(ValueError, match="outside"):
        ModelDownloadRedirectPolicy().redirect_request(
            urllib.request.Request(request().url), None, 302, "", {}, "http://169.254.169.254/latest/meta-data"
        )


def tensor_file():
    header = json.dumps({"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    return struct.pack("<Q", len(header)) + header + b"\x00\x00\x00\x00"


class DownloadResponse(io.BytesIO):
    def __init__(self, data, size=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if size is None else size)}


def test_worker_streams_verifies_and_atomically_publishes(tmp_path):
    (tmp_path / "models").mkdir()
    opener = Mock()
    opener.open.return_value = DownloadResponse(tensor_file())
    events = []
    worker = GpuModelDownloadWorker(tmp_path, opener=opener)
    worker.download(request(), lambda state, **fields: events.append(state))
    target = tmp_path / "models/diffusion_models" / request().filename
    assert target.read_bytes() == tensor_file()
    assert not list(target.parent.glob("*.agentd-part"))
    assert events == ["downloading", "verifying"]
    worker.download(request(), lambda *a, **k: None)
    assert opener.open.call_count == 1


def test_worker_never_publishes_a_truncated_download(tmp_path):
    (tmp_path / "models").mkdir()
    opener = Mock()
    opener.open.side_effect = [DownloadResponse(tensor_file()[:-1], len(tensor_file())) for _ in range(3)]
    worker = GpuModelDownloadWorker(tmp_path, opener=opener, sleep=lambda _: None)
    with pytest.raises(OSError, match="Incomplete"):
        worker.download(request(), lambda *a, **k: None)
    assert list((tmp_path / "models/diffusion_models").iterdir()) == []


def test_worker_does_not_retry_a_permanent_source_error(tmp_path):
    (tmp_path / "models").mkdir()
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError(request().url, 400, "bad", {}, None)
    with pytest.raises(urllib.error.HTTPError):
        GpuModelDownloadWorker(tmp_path, opener=opener).download(request(), lambda *a, **k: None)
    assert opener.open.call_count == 1


def test_worker_rejects_html_instead_of_weights(tmp_path):
    path = tmp_path / "fake.safetensors"
    path.write_bytes(b"<html>Access denied</html>")
    with pytest.raises(ValueError, match="header"):
        GpuModelDownloadWorker.verify(path)


def test_worker_writes_terminal_status_without_leaking_source_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_model_download_worker, "fcntl", SimpleNamespace(
        flock=Mock(), LOCK_EX=2, LOCK_NB=4), raising=False)
    (tmp_path / "models").mkdir()
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError("https://cdn.hf.co/file?signature=private", 403, "denied", {}, None)
    worker = GpuModelDownloadWorker(tmp_path, opener=opener)
    worker.run(request().as_dict(), "attempt-1")
    status = json.loads((tmp_path / "temp/agentd-model-downloads" / (request().job_id + ".json")).read_text())
    assert status["state"] == "failed"
    assert status["attempt_id"] == "attempt-1"
    assert "403" in status["error"]
    assert "signature" not in json.dumps(status)


def test_worker_deduplicates_an_already_locked_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_model_download_worker, "fcntl", SimpleNamespace(
        flock=Mock(side_effect=BlockingIOError()), LOCK_EX=2, LOCK_NB=4), raising=False)
    opener = Mock()
    GpuModelDownloadWorker(tmp_path, opener=opener).run(request().as_dict())
    opener.open.assert_not_called()


def client(**overrides):
    conn = {"url": "http://gpu:8188", "portal_url": "http://gpu:1111", "auth": "Bearer test-only"}
    args = dict(fetch=Mock(return_value=response({"status": "started"})), connection=lambda: conn,
                current_connection=lambda: conn, get=Mock(return_value=response({}, 404)), lease=Mock(),
                ready=lambda _: False)
    args.update(overrides)
    return GpuModelDownloadClient(**args)


def test_portal_uses_authenticated_fixed_worker_not_agent_shell():
    send = Mock(return_value=response({"status": "started"}))
    downloader = client(fetch=send)
    downloader.start(request())
    args, kwargs = send.call_args
    assert args[0] == "http://gpu:1111/capabilities/provision"
    assert kwargs["headers"]["Authorization"] == "Bearer test-only"
    manifest = json.loads(kwargs["json"]["inline_yaml"])
    assert manifest["on_failure"]["action"] == "continue"
    shell = shlex.split(manifest["post_commands"][0])
    assert shell[:2] == ["python3", "-c"]
    compile(shell[2], "gpu-bootstrap", "exec")
    assert "Bearer test-only" not in shell[2]
    assert request().url not in shell[2]  # encoded as data, not executable text


def test_downloader_refuses_a_changed_rental():
    send = Mock()
    downloader = client(fetch=send, current_connection=lambda: {"url": "http://old:8188", "auth": "old"})
    with pytest.raises(ValueError, match="changed"):
        downloader.start(request())
    send.assert_not_called()


@pytest.mark.asyncio
async def test_gpu_failure_surfaces_without_waiting_for_other_files():
    status = {"state": "failed", "source_id": request().source_id, "error": "HTTP 404", "updated_at": time.time()}
    downloader = client(get=Mock(return_value=response(status)))
    with pytest.raises(ValueError, match="HTTP 404"):
        await downloader.wait([request(), request("other.safetensors")], asyncio.Event())


def test_retry_ignores_previous_failure_until_new_worker_reports():
    old = {"state": "failed", "source_id": request().source_id, "attempt_id": "old", "updated_at": time.time()}
    downloader = client(get=Mock(return_value=response(old)))
    downloader.start(request())
    assert downloader.status(request()) is None


def test_corrected_source_can_retry_a_failed_destination():
    old = {"state": "failed", "source_id": "previous-wrong-source", "updated_at": time.time()}
    send = Mock(return_value=response({"status": "started"}))
    downloader = client(fetch=send, get=Mock(return_value=response(old)))
    downloader.start(request())
    send.assert_called_once()


def test_active_destination_cannot_be_replaced_by_a_different_source():
    active = {"state": "downloading", "source_id": "different-source", "updated_at": time.time()}
    send = Mock()
    downloader = client(fetch=send, get=Mock(return_value=response(active)))
    with pytest.raises(ValueError, match="another source"):
        downloader.start(request())
    send.assert_not_called()


def installer(**overrides):
    args = dict(catalog=lambda: [], loadable=lambda: {}, submit=Mock(), start_manager=Mock(),
                manager_busy=lambda: False, queued_recently=lambda _: False, mark_queued=Mock(),
                wait_manager=AsyncMock(return_value="idle"),
                await_loadable=AsyncMock(side_effect=lambda names, abort: {n: n for n in names}), lease=Mock(),
                direct=SimpleNamespace(start=Mock(), wait=AsyncMock(), active=Mock(return_value=False)))
    args.update(overrides)
    return ModelInstallationService(**args)


@pytest.mark.asyncio
async def test_uncatalogued_qwen_automatically_uses_gpu():
    service = installer()
    progress = []
    result = await service.install([request().as_dict()], asyncio.Event(), progress.append)
    service.submit.assert_not_called()
    service.direct.start.assert_called_once_with(request())
    assert request().filename in result
    assert "absent from Manager" in progress[0]


@pytest.mark.asyncio
async def test_catalogued_file_stays_on_manager():
    entry = {"filename": request().filename, "url": request().url}
    service = installer(catalog=lambda: [entry])
    await service.install([request().as_dict()], asyncio.Event(), lambda _: None)
    service.submit.assert_called_once_with(request().as_dict(), entry)
    service.start_manager.assert_called_once()
    service.direct.start.assert_not_called()


@pytest.mark.asyncio
async def test_manager_400_falls_back_to_direct_download():
    service = installer(catalog=lambda: [{"filename": request().filename}],
                        submit=Mock(side_effect=ValueError("HTTP 400: Invalid model install request")))
    progress = []
    result = await service.install([request().as_dict()], asyncio.Event(), progress.append)
    service.wait_manager.assert_not_awaited()
    service.direct.start.assert_called_once_with(request())
    assert request().filename in result
    assert "HTTP 400" in progress[0]


@pytest.mark.asyncio
async def test_failed_direct_download_cancels_manager_wait_not_the_gpu_job():
    manager_cancelled = asyncio.Event()
    async def manager(*args):
        try:
            await asyncio.Event().wait()
        finally:
            manager_cancelled.set()
    other = request("vae.safetensors", "vae")
    service = installer(catalog=lambda: [{"filename": other.filename}], wait_manager=manager,
                        direct=SimpleNamespace(start=Mock(), wait=AsyncMock(side_effect=ValueError("HTTP 404")),
                                               active=Mock(return_value=False)))
    with pytest.raises(ValueError, match="404"):
        await asyncio.wait_for(service.install([request().as_dict(), other.as_dict()], asyncio.Event(), lambda _: None), 1)
    assert manager_cancelled.is_set()


@pytest.mark.asyncio
async def test_accepted_but_not_loadable_is_never_success():
    service = installer(await_loadable=AsyncMock(return_value={}))
    with pytest.raises(ValueError, match="NOT confirmed"):
        await service.install([request().as_dict()], asyncio.Event(), lambda _: None)


@pytest.mark.asyncio
async def test_tool_falls_back_from_manager_400(monkeypatch):
    monkeypatch.setattr(comfy_bridge, "_manager_catalog", lambda: [{"filename": request().filename}])
    monkeypatch.setattr(comfy_bridge, "_loadable_names", lambda: {})
    monkeypatch.setattr(comfy_bridge, "_lease", lambda _: None)
    monkeypatch.setattr(comfy_bridge, "_manager_busy", lambda: False)
    direct = SimpleNamespace(start=Mock(), wait=AsyncMock(), active=Mock(return_value=False))
    monkeypatch.setattr(comfy_bridge, "GpuModelDownloadClient", lambda **kwargs: direct)
    monkeypatch.setattr(comfy_bridge, "_await_loadable", AsyncMock(return_value={request().filename: request().filename}))
    monkeypatch.setattr(comfy_bridge, "_post", lambda *a, **k: response({"error": "Invalid model install request"}, 400))
    monkeypatch.setattr(comfy_bridge.studio_state, "install_allowed", lambda _: (True, ""))
    result = await comfy_bridge.ComfyInstallTool().execute("test", {"files": [request().as_dict()]}, asyncio.Event())
    assert not result.is_error
    direct.start.assert_called_once_with(request())


def graph(vae="qwen_image_vae.safetensors"):
    return {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": request().filename}},
            "2": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors"}}}


def test_wrong_vae_is_rejected_even_when_both_files_exist():
    bad = WorkflowModelManifest.from_graph(graph("ae.safetensors"))
    errors = bad.compare(WorkflowModelManifest.from_graph(graph()))
    assert "VAE mismatch" in errors[0]
    assert "qwen_image_vae.safetensors" in errors[0]


def test_a_flux_reference_cannot_authorise_qwen_with_flux_vae():
    reference = graph("ae.safetensors")
    reference["1"]["inputs"]["unet_name"] = "flux1-dev.safetensors"
    errors = WorkflowModelManifest.from_graph(graph("ae.safetensors")).compare(
        WorkflowModelManifest.from_graph(reference))
    assert "different model/version" in errors[0]


def test_official_bf16_reference_accepts_same_model_fp8_variant():
    reference = graph()
    reference["1"]["inputs"]["unet_name"] = "qwen_image_edit_2511_bf16.safetensors"
    assert WorkflowModelManifest.from_graph(graph()).compare(WorkflowModelManifest.from_graph(reference)) == []


def test_reference_parser_understands_official_subgraphs():
    nodes = [{"type": node["class_type"], "widgets_values": list(node["inputs"].values())} for node in graph().values()]
    template = {"nodes": [{"type": "qwen-subgraph"}],
                "definitions": {"subgraphs": [{"id": "qwen-subgraph", "nodes": nodes},
                                               {"id": "unused", "nodes": [{"type": "VAELoader", "widgets_values": ["ae.safetensors"]}]}]}}
    assert WorkflowModelManifest.from_graph(template) == WorkflowModelManifest.from_graph(graph())


def test_reference_is_fetched_and_reused_only_for_same_stack(tmp_path):
    fetch = Mock(return_value=response(graph()))
    repository = WorkflowReferenceRepository(tmp_path, fetch=fetch)
    assert repository.check(graph())
    assert repository.check(graph(), "https://raw.githubusercontent.com/Comfy-Org/templates/main/qwen.json") == []
    assert repository.check(graph()) == []
    assert repository.check(graph("ae.safetensors"))
    fetch.assert_called_once()


@pytest.mark.asyncio
async def test_bad_companions_clear_previous_install_permission_before_instance_io(tmp_path, monkeypatch):
    path = tmp_path / "stills.api.json"
    path.write_text(json.dumps(graph("ae.safetensors")))
    forget = Mock()
    get = Mock(side_effect=AssertionError("must reject before contacting GPU"))
    monkeypatch.setattr(comfy_bridge.studio_state, "forget_validated", forget)
    monkeypatch.setattr(comfy_bridge, "current_workspace", lambda *a: str(tmp_path))
    monkeypatch.setattr(comfy_bridge, "fetch", lambda *a, **k: response(graph()))
    monkeypatch.setattr(comfy_bridge, "_get", get)
    result = await comfy_bridge.ComfyValidateTool().execute("test", {
        "workflow_path": str(path), "reference_workflow_url": "https://raw.githubusercontent.com/org/repo/main/qwen.json",
    }, asyncio.Event())
    assert result.is_error
    assert "VAE mismatch" in result.content[0].text
    forget.assert_called_once()
    get.assert_not_called()
