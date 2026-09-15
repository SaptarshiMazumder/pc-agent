"""Real streaming/verification behavior for ordinary public model hosts."""

import io
import json
import struct
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import gpu_model_download_worker
from gpu_model_download_worker import GpuModelDownloadWorker
from model_download_request import ModelDownloadRequest
from model_download_redirect_policy import ModelDownloadRedirectPolicy


def tensor_bytes():
    header = json.dumps({"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    return struct.pack("<Q", len(header)) + header + bytes(4)


def source(data, length=False):
    stream = io.BytesIO(data)
    stream.headers = {"Content-Length": str(len(data))} if length else {}
    return stream


def model():
    return ModelDownloadRequest("lora.safetensors", "https://publisher.example/download/12", "lora")


@pytest.mark.parametrize("name", ["Hands zib v1.safetensors", "人物 LoRA (v2) [XL].safetensors"])
def test_public_model_names_can_contain_spaces_and_unicode(name):
    assert ModelDownloadRequest(name, model().url, "lora").filename == name


def test_public_download_without_content_length_streams_and_verifies(tmp_path):
    (tmp_path / "models").mkdir()
    opener = Mock()
    opener.open.return_value = source(tensor_bytes())
    GpuModelDownloadWorker(tmp_path, opener=opener).download(model(), lambda *a, **k: None)
    assert (tmp_path / "models/loras/lora.safetensors").read_bytes() == tensor_bytes()
    request = opener.open.call_args.args[0]
    assert request.get_header("User-agent") == "agentd-model-downloader/1.0"
    assert request.get_header("Accept-encoding") == "identity"
    assert not request.has_header("Authorization")


def test_chunked_truncated_weights_are_never_published(tmp_path):
    (tmp_path / "models").mkdir()
    opener = Mock()
    opener.open.return_value = source(tensor_bytes()[:-1])
    with pytest.raises(ValueError, match="incomplete"):
        GpuModelDownloadWorker(tmp_path, opener=opener).download(model(), lambda *a, **k: None)
    assert list((tmp_path / "models/loras").iterdir()) == []


def test_webpage_is_not_a_successful_model_download(tmp_path):
    (tmp_path / "models").mkdir()
    stream = source(b"<html>Login</html>")
    stream.headers["Content-Type"] = "text/html"
    with pytest.raises(ValueError, match="web/login page"):
        GpuModelDownloadWorker(tmp_path, opener=Mock(open=Mock(return_value=stream))).download(model(), lambda *a, **k: None)


def test_unknown_size_preserves_disk_reserve(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(gpu_model_download_worker.shutil, "disk_usage", lambda _: SimpleNamespace(free=256 * 1024 * 1024 + 12))
    opener = Mock(open=Mock(return_value=source(tensor_bytes())))
    with pytest.raises(ValueError, match="disk space"):
        GpuModelDownloadWorker(tmp_path, opener=opener).download(model(), lambda *a, **k: None)
    assert list((tmp_path / "models/loras").iterdir()) == []


def test_redirect_keeps_app_header_but_drops_credentials():
    request = urllib.request.Request(model().url, headers={**model().headers(),
                                     "Authorization": "secret", "Cookie": "private"})
    redirected = ModelDownloadRedirectPolicy().redirect_request(
        request, None, 302, "", {}, "https://cdn.other.example/model")
    assert redirected.get_header("User-agent") == model().USER_AGENT
    assert not redirected.has_header("Authorization")
    assert not redirected.has_header("Cookie")


def test_retryable_server_failure_retries_and_publishes(tmp_path):
    (tmp_path / "models").mkdir()
    opener = Mock()
    opener.open.side_effect = [urllib.error.HTTPError(model().url, 503, "unavailable", {}, None), source(tensor_bytes())]
    GpuModelDownloadWorker(tmp_path, opener=opener, sleep=lambda _: None).download(model(), lambda *a, **k: None)
    assert (tmp_path / "models/loras/lora.safetensors").exists()
    assert opener.open.call_count == 2


def test_third_party_auth_refusal_is_not_retried_with_credentials(tmp_path):
    (tmp_path / "models").mkdir()
    opener = Mock(open=Mock(side_effect=urllib.error.HTTPError(model().url, 401, "login", {}, None)))
    with pytest.raises(urllib.error.HTTPError):
        GpuModelDownloadWorker(tmp_path, opener=opener).download(model(), lambda *a, **k: None)
    opener.open.assert_called_once()
    assert not opener.open.call_args.args[0].has_header("Authorization")
