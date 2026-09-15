"""Backend-independent recovery and the run-time missing-model gate."""

import asyncio
import io
import json
import struct
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import comfy_bridge
from gpu_model_download_client import GpuModelDownloadClient
from gpu_model_download_worker import GpuModelDownloadWorker
from model_download_request import ModelDownloadRequest
from model_installation_service import ModelInstallationService
from model_readiness import ModelReadiness
from agent_runtime.infrastructure.net.outbound import Response


def model(filename="companion.safetensors", kind="vae"):
    return ModelDownloadRequest(filename, f"https://huggingface.co/publisher/model/resolve/main/{filename}", kind)


def inventory(names=(), field="vae_name"):
    return {"Loader": {"input": {"required": {field: [list(names)]}}}}


def service(**overrides):
    values = dict(catalog=lambda: [{"filename": model().filename}], loadable=lambda: {},
                  submit=Mock(), start_manager=Mock(), manager_busy=lambda: False,
                  queued_recently=lambda _: False, mark_queued=Mock(),
                  wait_manager=AsyncMock(return_value="idle"),
                  await_loadable=AsyncMock(side_effect=[{}, {model().filename: model().filename}]),
                  lease=Mock(), direct=SimpleNamespace(start=Mock(), wait=AsyncMock(), active=Mock(return_value=False)))
    values.update(overrides)
    return ModelInstallationService(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("filename,kind", [("companion.safetensors", "vae"),
                                          ("weights.safetensors", "unet"),
                                          ("encoder.safetensors", "clip")])
async def test_idle_manager_missing_file_uses_gpu_and_verifies(filename, kind):
    request = model(filename, kind)
    installer = service(catalog=lambda: [{"filename": filename}],
                        await_loadable=AsyncMock(side_effect=[{}, {filename: "subfolder/" + filename}]))
    progress = []
    result = await installer.install([request.as_dict()], asyncio.Event(), progress.append)
    installer.direct.start.assert_called_once_with(request)
    assert result[filename] == "subfolder/" + filename
    assert any("retrying with GPU-side" in message for message in progress)


@pytest.mark.asyncio
async def test_successful_manager_does_not_download_again():
    installer = service(await_loadable=AsyncMock(return_value={model().filename: model().filename}))
    await installer.install([model().as_dict()], asyncio.Event(), lambda _: None)
    installer.direct.start.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", [True, None])
async def test_unknown_or_active_manager_never_starts_competing_writer(busy):
    installer = service(manager_busy=lambda: busy)
    with pytest.raises(ValueError, match="no duplicate"):
        await installer.install([model().as_dict()], asyncio.Event(), lambda _: None)
    installer.direct.start.assert_not_called()


@pytest.mark.asyncio
async def test_lost_manager_status_does_not_trigger_duplicate():
    installer = service(wait_manager=AsyncMock(return_value="unreachable"))
    with pytest.raises(ValueError, match="downloads may continue"):
        await installer.install([model().as_dict()], asyncio.Event(), lambda _: None)
    installer.direct.start.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_failure_preserves_actual_error():
    async def wait(requests, *_):
        if requests:
            raise ValueError("Source returned HTTP 403")
    installer = service(direct=SimpleNamespace(start=Mock(), wait=wait, active=Mock(return_value=False)))
    with pytest.raises(ValueError, match="HTTP 403"):
        await installer.install([model().as_dict()], asyncio.Event(), lambda _: None)
    installer.direct.start.assert_called_once()


@pytest.mark.asyncio
async def test_fallback_is_bounded_and_missing_is_not_reported_as_downloaded():
    installer = service(await_loadable=AsyncMock(return_value={}))
    with pytest.raises(ValueError, match="NOT confirmed") as error:
        await installer.install([model().as_dict()], asyncio.Event(), lambda _: None)
    installer.direct.start.assert_called_once()
    assert "Download finished" not in str(error.value)


@pytest.mark.asyncio
async def test_fallback_does_not_relax_url_security():
    # Any HTTPS host is allowed now (the allowlist was opened on purpose); what the fallback
    # must still refuse is a link that is not plain TLS — here, plain HTTP.
    payload = {**model().as_dict(), "url": "http://untrusted.test/companion.safetensors"}
    installer = service()
    with pytest.raises(ValueError, match="fallback unavailable"):
        await installer.install([payload], asyncio.Event(), lambda _: None)
    installer.direct.start.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_install_does_not_launch_fallback():
    abort = asyncio.Event()
    async def verify(*_):
        abort.set()
        return {}
    installer = service(await_loadable=verify)
    with pytest.raises(ValueError, match="cancelled"):
        await installer.install([model().as_dict()], abort, lambda _: None)
    installer.direct.start.assert_not_called()


@pytest.mark.asyncio
async def test_retry_observes_existing_fallback_instead_of_submitting_to_manager():
    direct = SimpleNamespace(start=Mock(), wait=AsyncMock(), active=Mock(return_value=True))
    installer = service(direct=direct, await_loadable=AsyncMock(return_value={model().filename: model().filename}))
    await installer.install([model().as_dict()], asyncio.Event(), lambda _: None)
    installer.submit.assert_not_called()
    direct.start.assert_not_called()
    direct.wait.assert_awaited_once()
    assert direct.wait.await_args.args[0] == [model()]


def downloader(*, ready, status=None):
    ticks = [0]
    async def sleep(_):
        ticks[0] += 125
    conn = {"url": "http://gpu", "portal_url": "http://portal", "auth": "test"}
    return GpuModelDownloadClient(
        fetch=Mock(return_value=Response(status=200, text='{"status":"started"}')),
        connection=lambda: conn, current_connection=lambda: conn,
        get=Mock(return_value=Response(status=200 if status else 404, text=json.dumps(status or {}))),
        ready=ready, lease=Mock(), sleep=sleep, clock=lambda: ticks[0],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_lost_tracking_reconciles_inventory_without_redownload(stale):
    status = {"source_id": model().source_id, "state": "downloading", "updated_at": time.time() - 200} if stale else None
    client = downloader(ready=lambda _: True, status=status)
    progress = []
    await client.wait([model()], asyncio.Event(), progress.append)
    client._fetch.assert_not_called()
    assert any("ComfyUI confirms" in message for message in progress)


@pytest.mark.asyncio
async def test_lost_tracking_and_missing_file_is_unknown_not_success():
    client = downloader(ready=lambda _: False)
    with pytest.raises(ValueError, match="may still be running"):
        await client.wait([model()], asyncio.Event())
    client._fetch.assert_not_called()


def test_old_done_status_does_not_hide_deleted_file():
    client = downloader(ready=lambda _: False, status={
        "source_id": model().source_id, "state": "done", "updated_at": time.time(),
    })
    client.start(model())
    client._fetch.assert_called_once()


def test_readiness_checks_correct_loader_not_just_same_basename():
    state = ModelReadiness(inventory([model().filename], "unet_name"))
    assert not state.contains(model())
    graph = {"1": {"class_type": "Loader", "inputs": {"unet_name": "folder/" + model().filename}}}
    assert state.missing(graph)


@pytest.mark.parametrize("choices", [[], ["pixel_space"], ["other.safetensors"]])
def test_empty_or_sentinel_only_loader_still_blocks_missing_model(choices):
    graph = {"1": {"class_type": "Loader", "inputs": {"vae_name": model().filename}}}
    assert ModelReadiness(inventory(choices)).missing(graph)


def test_custom_loader_and_exact_subfolder_names():
    filename = "publisher/" + model().filename
    state = ModelReadiness(inventory([filename], "custom_weight"))
    graph = {"1": {"class_type": "Loader", "inputs": {"custom_weight": filename}}}
    assert not state.missing(graph)
    assert state.names()[model().filename] == filename


@pytest.mark.asyncio
async def test_missing_vae_fallback_really_writes_model_file(tmp_path):
    # Real worker and filesystem, only the network source is faked.
    (tmp_path / "models").mkdir()
    header = json.dumps({"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    data = struct.pack("<Q", len(header)) + header + b"\x00" * 4
    source = io.BytesIO(data)
    source.headers = {"Content-Length": str(len(data))}
    opener = Mock()
    opener.open.return_value = source
    worker = GpuModelDownloadWorker(tmp_path, opener=opener)
    target = tmp_path / "models/vae" / model().filename
    async def verify(*_):
        return {model().filename: model().filename} if target.exists() else {}
    direct = SimpleNamespace(start=lambda request: worker.download(request, lambda *a, **k: None),
                             wait=AsyncMock(), active=lambda _: False)
    installer = service(direct=direct, await_loadable=verify)
    result = await installer.install([model().as_dict()], asyncio.Event(), lambda _: None)
    assert target.read_bytes() == data
    assert model().filename in result
    opener.open.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,status", [(inventory(), 200), ({}, 200), ({}, 503)])
async def test_run_blocks_missing_or_unknown_models_before_upload_or_billing(tmp_path, monkeypatch, payload, status):
    path = tmp_path / "stills.api.json"
    path.write_text(json.dumps({"1": {"class_type": "Loader", "inputs": {"vae_name": model().filename}}}))
    monkeypatch.setattr(comfy_bridge.studio_state, "checkpoint_answered", lambda _: (True, ""))
    monkeypatch.setattr(comfy_bridge, "_get", lambda *a, **k: Response(status=status, text=json.dumps(payload)))
    post, charge, upload = Mock(), Mock(), Mock()
    monkeypatch.setattr(comfy_bridge, "_post", post)
    monkeypatch.setattr(comfy_bridge, "_charge", charge)
    monkeypatch.setattr(comfy_bridge, "_push_reference", upload)
    result = await comfy_bridge.ComfyRunTool().execute("test", {"workflow_path": str(path)}, asyncio.Event())
    assert result.is_error
    post.assert_not_called()
    charge.assert_not_called()
    upload.assert_not_called()


@pytest.mark.asyncio
async def test_run_submits_after_required_models_are_present(tmp_path, monkeypatch):
    path = tmp_path / "stills.api.json"
    path.write_text(json.dumps({"1": {"class_type": "Loader", "inputs": {"vae_name": model().filename}}}))
    monkeypatch.setattr(comfy_bridge.studio_state, "checkpoint_answered", lambda _: (True, ""))
    monkeypatch.setattr(comfy_bridge, "_get", lambda *a, **k: Response(status=200, text=json.dumps(inventory([model().filename]))))
    monkeypatch.setattr(comfy_bridge, "_api_node_flags", lambda _: {})
    monkeypatch.setattr(comfy_bridge, "_quote_for", lambda *a: SimpleNamespace(paid=False, unpriced=[]))
    monkeypatch.setattr(comfy_bridge, "_unpriced_partner_nodes", lambda *a: [])
    monkeypatch.setattr(comfy_bridge, "_lease", lambda _: None)
    monkeypatch.setattr(comfy_bridge.studio_state, "run_started", lambda *a: None)
    monkeypatch.setattr(comfy_bridge, "_poll_run", AsyncMock(return_value=None))
    post = Mock(return_value=Response(status=200, text='{"prompt_id":"test-render"}'))
    monkeypatch.setattr(comfy_bridge, "_post", post)
    result = await comfy_bridge.ComfyRunTool().execute("test", {"workflow_path": str(path)}, asyncio.Event())
    assert not result.is_error, result.content[0].text
    post.assert_called_once()
    assert post.call_args.args[0] == "/api/prompt"


@pytest.mark.parametrize("status", [{}, {"is_processing": False},
                                  {"is_processing": False, "in_progress_count": 0}])
def test_manager_unknown_is_not_idle(monkeypatch, status):
    monkeypatch.setattr(comfy_bridge, "_queue_info", lambda: status)
    assert comfy_bridge._manager_busy() is (False if "in_progress_count" in status else None)
