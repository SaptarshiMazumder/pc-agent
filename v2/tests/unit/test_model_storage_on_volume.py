"""On the person's own Vast machine, models go to its volume if one is attached — so they persist."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock

from agent_runtime.infrastructure.net.outbound import Response
from gpu_model_download_client import GpuModelDownloadClient
from gpu_model_download_worker import GpuModelDownloadWorker
from model_download_request import ModelDownloadRequest
from model_storage_detector import DISK, VOLUME, WORKSPACE_VOLUME, ModelStorageDetector
from model_storage_setup import ModelStorageSetup
from model_storage_setup_client import PENDING, ModelStorageSetupClient

MOUNTS = """overlay / overlay rw 0 0
proc /proc proc rw 0 0
tmpfs /dev/shm tmpfs rw 0 0
/dev/nvme0n1 /etc/hosts ext4 rw 0 0
/dev/sdb /data ext4 rw 0 0
/dev/sdc /scratch xfs rw 0 0
"""
DEVICES = {"/": 1, "/workspace": 1, "/data": 2, "/scratch": 3}
FREE = {"/data": 500e9, "/scratch": 50e9}


def detector(devices=DEVICES, mounts=MOUNTS):
    return ModelStorageDetector(
        PurePosixPath("/workspace"), mounts=mounts,
        stat=lambda p: SimpleNamespace(st_dev=devices[str(p)]),
        disk_usage=lambda p: SimpleNamespace(free=FREE[p]),
        writable=lambda p: True, is_dir=lambda p: p in devices,
    )


def test_a_volume_elsewhere_is_found_and_the_roomiest_wins():
    assert detector().detect() == {"kind": VOLUME, "path": "/data"}


def test_a_volume_at_workspace_needs_nothing():
    assert detector({**DEVICES, "/workspace": 9}).detect()["kind"] == WORKSPACE_VOLUME


def test_no_volume_is_the_disk():
    assert detector(mounts="overlay / overlay rw 0 0\n").detect() == {"kind": DISK, "path": ""}


def _setup(tmp_path, found):
    restart, ready = Mock(), Mock()
    fake = SimpleNamespace(detect=lambda: found)
    return ModelStorageSetup(tmp_path / "ComfyUI", fake, restart=restart, wait_ready=ready), restart


def test_comfyui_is_pointed_at_the_volume_once(tmp_path):
    (tmp_path / "ComfyUI").mkdir()
    (tmp_path / "ComfyUI/extra_model_paths.yaml").write_text("mine:\n    base_path: /x\n")
    setup, restart = _setup(tmp_path, {"kind": VOLUME, "path": str(tmp_path / "vol")})
    first = setup.ensure()
    assert first["models_dir"] == str(tmp_path / "vol/ComfyUI/models") and first["restarted"]
    assert (tmp_path / "vol/ComfyUI/models/diffusion_models").is_dir()
    yaml = (tmp_path / "ComfyUI/extra_model_paths.yaml").read_text()
    assert "mine:" in yaml and "agentd_volume:" in yaml
    assert json.dumps(str(tmp_path / "vol" / "ComfyUI" / "models")) in yaml
    assert setup.ensure()["restarted"] is False
    restart.assert_called_once()


def test_on_disk_nothing_is_touched(tmp_path):
    (tmp_path / "ComfyUI").mkdir()
    setup, restart = _setup(tmp_path, {"kind": DISK, "path": ""})
    assert setup.ensure()["models_dir"] == str(tmp_path / "ComfyUI/models")
    assert not (tmp_path / "ComfyUI/extra_model_paths.yaml").exists()
    restart.assert_not_called()


def test_the_worker_downloads_into_the_volume_only_when_asked(tmp_path, monkeypatch):
    import gpu_model_download_worker

    monkeypatch.setattr(gpu_model_download_worker, "fcntl",
                        SimpleNamespace(flock=lambda *a: None, LOCK_EX=0, LOCK_NB=0), raising=False)
    (tmp_path / "models").mkdir()
    volume_models = tmp_path / "vol/models"
    volume_models.mkdir(parents=True)
    storage = Mock(return_value=SimpleNamespace(ensure=lambda: {"models_dir": str(volume_models)}))
    worker = GpuModelDownloadWorker(tmp_path, use_volume=True, storage=storage)
    worker.download = Mock()
    request = ModelDownloadRequest("x.safetensors", "https://example.com/x.safetensors", "vae")
    worker.run(request.as_dict())
    assert worker.models == volume_models
    rented = GpuModelDownloadWorker(tmp_path, storage=storage)
    rented.download = Mock()
    rented.run(request.as_dict())
    assert rented.models == tmp_path / "models"
    storage.assert_called_once()


def test_the_rented_gpu_command_never_asks_for_the_volume():
    request = ModelDownloadRequest("x.safetensors", "https://example.com/x.safetensors", "vae")
    import base64, re, shlex
    code = shlex.split(GpuModelDownloadClient.command(request))[2]
    data = json.loads(base64.b64decode(re.search(r"data=json.loads\(base64.b64decode\('([^']+)'", code).group(1)))
    assert data["use_volume"] is False


def test_a_storage_check_that_has_not_answered_is_pending():
    fetch = Mock(side_effect=lambda url, **kw: Response(status=200, text='{"status": "started"}')
                 if url.endswith("/provision") else Response(status=404, text=""))
    clock = iter([0, 0, 100])
    client = ModelStorageSetupClient(fetch=fetch, sleep=lambda _: None, clock=lambda: next(clock))
    answer = client.run({"portal_url": "https://p", "url": "https://c", "auth": "Bearer t"}, wait_s=10)
    assert answer["kind"] == PENDING and answer["nonce"]
