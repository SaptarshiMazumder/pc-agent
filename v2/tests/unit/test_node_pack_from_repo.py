"""A node pack Manager's registry does not list installs from its repository, on the machine."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import comfy_bridge
import gpu_node_pack_worker
from agent_runtime.infrastructure.net.outbound import Response
from gpu_node_pack_install_client import GpuNodePackInstallClient
from gpu_node_pack_worker import GpuNodePackWorker

REPO = "https://github.com/OsamaAtiq12/ComfyUI-Krea-MultiShot-Stills"


@pytest.fixture(autouse=True)
def _no_install_panel_writes(monkeypatch):
    """The install panel's rows go to the workspace's studio file; a test has none."""
    monkeypatch.setattr(comfy_bridge.studio_state, "set_install_progress", lambda kind, rows: None)


@pytest.mark.parametrize("bad", ["http://github.com/a/b", "https://evil.com/a/b", "https://github.com/a",
                                 "https://github.com/a/b/../c", "https://user@github.com/a/b",
                                 "https://github.com:8443/a/b"])
def test_only_a_plain_repository_page_is_accepted(bad):
    with pytest.raises(ValueError):
        gpu_node_pack_worker.repository(bad)


def test_the_clone_url_and_folder_come_from_the_link():
    assert gpu_node_pack_worker.repository(REPO + ".git") == (REPO + ".git", "ComfyUI-Krea-MultiShot-Stills")


def _worker(tmp_path, returncode=0):
    commands = []

    def run(command, **kw):
        commands.append(command)
        if command[:2] == ["git", "clone"]:
            dest = tmp_path / "custom_nodes" / "ComfyUI-Krea-MultiShot-Stills"
            dest.mkdir(parents=True)
            (dest / "requirements.txt").write_text("einops\n")
        return SimpleNamespace(returncode=returncode, stdout="", stderr="boom")

    control = Mock()
    return GpuNodePackWorker(tmp_path, run=run, control=control, python="py"), commands, control


def _status(tmp_path, job):
    return json.loads((tmp_path / "temp" / gpu_node_pack_worker.STATUS_DIR / f"{job}.json").read_text())


def test_the_worker_clones_installs_requirements_and_restarts(tmp_path):
    worker, commands, control = _worker(tmp_path)
    worker.run(REPO, "j1")
    assert commands[0][:3] == ["git", "clone", "--depth"]
    assert commands[1] == ["py", "-m", "pip", "install", "-r",
                           str(tmp_path / "custom_nodes/ComfyUI-Krea-MultiShot-Stills/requirements.txt")]
    control.restart.assert_called_once()
    control.wait_ready.assert_called_once()
    assert _status(tmp_path, "j1") == {**_status(tmp_path, "j1"), "state": "done", "folder": "ComfyUI-Krea-MultiShot-Stills"}


def test_a_failed_step_is_reported_and_nothing_restarts(tmp_path):
    worker, _, control = _worker(tmp_path, returncode=1)
    worker.run(REPO, "j2")
    assert _status(tmp_path, "j2")["state"] == "failed" and "boom" in _status(tmp_path, "j2")["error"]
    control.restart.assert_not_called()


def test_the_client_follows_the_install_through_the_restart():
    answers = iter([Response(status=404), Response(error="restarting"),
                    Response(status=200, text='{"state": "done", "folder": "X"}')])
    fetch = Mock(return_value=Response(status=200, text='{"status": "started"}'))
    client = GpuNodePackInstallClient(fetch=fetch, get=lambda *a, **k: next(answers), lease=Mock(),
                                      sleep=AsyncMock())
    status = asyncio.run(client.install({"portal_url": "http://p", "auth": "Bearer t"}, REPO, 60))
    assert status["folder"] == "X"
    assert fetch.call_args.args[0] == "http://p/capabilities/provision"


def test_a_pack_missing_from_the_registry_installs_from_its_repository(monkeypatch):
    monkeypatch.setattr(comfy_bridge.studio_state, "node_install_allowed", lambda: (True, ""))
    monkeypatch.setattr(comfy_bridge, "_manager_present", lambda: True)
    monkeypatch.setattr(comfy_bridge, "_node_catalog", lambda: ({"other": {"title": "Other"}}, ""))
    installed = comfy_bridge.ToolResult.text("installed")
    from_repo = AsyncMock(return_value=installed)
    monkeypatch.setattr(comfy_bridge.ComfyNodeInstallTool, "_install_from_repo", from_repo)
    result = asyncio.run(comfy_bridge.ComfyNodeInstallTool().execute("t", {"pack": REPO}, asyncio.Event()))
    assert result is installed
    assert from_repo.call_args.args[0] == REPO


def test_a_bare_comfyui_link_says_how_to_install_by_hand(monkeypatch):
    monkeypatch.setattr(comfy_bridge, "_override", lambda: {"kind": "user_url", "url": "http://x"})
    result = asyncio.run(comfy_bridge.ComfyNodeInstallTool._install_from_repo(REPO, None, None))
    assert result.is_error and "custom_nodes/ComfyUI-Krea-MultiShot-Stills" in result.content[0].text
