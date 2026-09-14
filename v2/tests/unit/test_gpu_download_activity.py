import json
from unittest.mock import Mock

import pytest

import comfy_bridge
from agent_runtime.infrastructure.net.outbound import Response
from gpu_download_activity import GpuDownloadActivity
from vast.infrastructure.http_instance_probe import HttpInstanceProbe


def test_terminal_worker_preserves_other_downloads_and_publishes_no_credentials(tmp_path):
    lock = Mock()
    first = GpuDownloadActivity(tmp_path, lock=lock, now=lambda: 1000)
    second = GpuDownloadActivity(tmp_path, lock=lock, now=lambda: 1001)
    first.update("one", "starting")
    second.update("two", "downloading")
    first.update("one", "done")
    status = json.loads((tmp_path / "activity.json").read_text())
    assert status == {"jobs": {"two": {"state": "downloading", "updated_at": 1001}}}
    probe = HttpInstanceProbe(now=lambda: 1002)
    probe._get_json = Mock(side_effect=[{"queue_running": [], "queue_pending": []}, probe._ABSENT, status])
    assert probe.busy("http://gpu") is True
    second.update("two", "failed")
    assert json.loads((tmp_path / "activity.json").read_text()) == {"jobs": {}}
    assert lock.call_count == 4


def test_bad_index_is_not_overwritten_as_false_idle(tmp_path):
    path = tmp_path / "activity.json"
    path.write_text("not json")
    with pytest.raises(ValueError):
        GpuDownloadActivity(tmp_path, lock=Mock()).update("one", "done")
    assert path.read_text() == "not json"


@pytest.mark.parametrize("status,body", [(200, {"alive": False}), (503, {}), (200, {})])
def test_unconfirmed_keepalive_blocks_gpu_submission(monkeypatch, status, body):
    monkeypatch.setattr(comfy_bridge, "current_account_id", lambda: "alice")
    monkeypatch.setattr(comfy_bridge, "fetch", Mock(return_value=Response(status=status, text=json.dumps(body))))
    with pytest.raises(RuntimeError, match="keepalive was not confirmed"):
        comfy_bridge._lease(180)


def test_confirmed_keepalive_allows_gpu_submission(monkeypatch):
    monkeypatch.setattr(comfy_bridge, "current_account_id", lambda: "alice")
    monkeypatch.setattr(comfy_bridge, "fetch", Mock(return_value=Response(status=200, text='{"alive":true}')))
    comfy_bridge._lease(180)
