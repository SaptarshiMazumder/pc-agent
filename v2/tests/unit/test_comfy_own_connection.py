"""A chat can use the person's own ComfyUI: their Vast machine, or any ComfyUI address."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest

import comfy_bridge
from agent_runtime.infrastructure.net.outbound import Response
from account_connection_repository import RENTED, USER_URL, USER_VAST, AccountConnectionRepository
from comfy_connection_probe import ComfyConnectionProbe
from comfy_connection_resolver import ComfyConnectionResolver
from manual_model_downloader import ManualModelDownloader
from model_installation_service import ModelInstallationService

STATS = Response(status=200, text='{"system": {"comfyui_version": "0.35.0"}}')


def _fetch(routes):
    calls = []

    def fetch(url, **kw):
        calls.append((url, kw))
        for suffix, response in routes.items():
            if url.endswith(suffix):
                return response
        return Response(status=404, text="")

    return fetch, calls


def test_a_vast_portal_link_finds_comfyui_and_keeps_the_portal():
    services = [{"name": "Instance Portal", "direct_url": "https://1.2.3.4:40001/"},
                {"name": "ComfyUI", "direct_url": "https://1.2.3.4:40002/"}]
    fetch, calls = _fetch({"/capabilities/services": Response(status=200, text=json.dumps(services)),
                           "/api/system_stats": STATS})
    record = ComfyConnectionProbe(fetch).probe("https://1.2.3.4:40001/?token=abc")
    assert record["kind"] == USER_VAST
    assert record["url"] == "https://1.2.3.4:40002"
    assert record["portal_url"] == "https://1.2.3.4:40001"
    assert record["auth"] == "Bearer abc"
    assert "abc" not in record["label"]
    assert calls[0][1]["headers"] == {"Authorization": "Bearer abc"}


def test_a_plain_comfyui_link_keeps_its_token_in_the_query():
    fetch, calls = _fetch({"/api/system_stats": STATS})
    record = ComfyConnectionProbe(fetch).probe("http://5.6.7.8:8188/?token=xyz")
    assert record["kind"] == USER_URL
    assert record["url"] == "http://5.6.7.8:8188#q=token=xyz"
    assert calls[-1][1]["params"] == {"token": "xyz"}


def test_an_address_that_is_not_comfyui_is_not_saved():
    fetch, _ = _fetch({"/api/system_stats": Response(status=200, text="<html>hi</html>")})
    with pytest.raises(ValueError, match="not a ComfyUI"):
        ComfyConnectionProbe(fetch).probe("http://5.6.7.8:8188")


def test_nothing_is_there_until_the_account_chooses(tmp_path):
    (tmp_path / ".studio").mkdir()
    (tmp_path / ".studio/connection.json").write_text('{"url": "http://rented:1", "auth": "Bearer r"}')
    repo = AccountConnectionRepository(tmp_path)
    resolver = ComfyConnectionResolver(tmp_path, repo)
    assert resolver.choice() is None and resolver.current() is None


def test_renting_is_used_only_once_approved(tmp_path):
    (tmp_path / ".studio").mkdir()
    (tmp_path / ".studio/connection.json").write_text('{"url": "http://rented:1", "auth": "Bearer r"}')
    repo = AccountConnectionRepository(tmp_path)
    repo.save({"kind": RENTED})
    resolver = ComfyConnectionResolver(tmp_path, repo)
    assert resolver.current()["url"] == "http://rented:1" and not resolver.is_own()


def test_the_account_s_own_machine_wins_over_the_rented_gpu(tmp_path):
    repo = AccountConnectionRepository(tmp_path)
    repo.save({"kind": USER_URL, "url": "http://mine:8188", "auth": "", "label": "http://mine:8188"})
    resolver = ComfyConnectionResolver(tmp_path, repo)
    assert resolver.current()["url"] == "http://mine:8188" and resolver.is_own()


def test_no_lease_on_the_persons_own_machine(monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(comfy_bridge, "fetch", fetch)
    monkeypatch.setattr(comfy_bridge, "current_account_id", lambda: "acct_1")
    monkeypatch.setattr(comfy_bridge, "_own_connection", lambda: True)
    comfy_bridge._lease(180)
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_without_a_downloader_a_catalogued_hf_file_goes_to_manager():
    file = {"filename": "x.safetensors", "kind": "vae",
            "url": "https://huggingface.co/o/r/resolve/main/x.safetensors"}
    submit = Mock()
    service = ModelInstallationService(
        catalog=lambda: [{"filename": "x.safetensors", "url": file["url"]}],
        loadable=Mock(side_effect=[{}, {"x.safetensors": "x.safetensors"}]),
        submit=submit, start_manager=Mock(), manager_busy=Mock(return_value=False),
        queued_recently=Mock(return_value=False), mark_queued=Mock(),
        wait_manager=AsyncMock(return_value="idle"),
        await_loadable=AsyncMock(return_value={"x.safetensors": "x.safetensors"}),
        lease=Mock(), direct=ManualModelDownloader(),
    )
    await service.install([file], None, lambda message: None)
    submit.assert_called_once()


def test_without_a_downloader_an_uncatalogued_file_says_what_to_do():
    from model_download_request import ModelDownloadRequest

    request = ModelDownloadRequest("x.safetensors", "https://example.com/x.safetensors", "vae")
    with pytest.raises(ValueError, match="models/vae"):
        ManualModelDownloader().start(request)


@pytest.mark.asyncio
async def test_own_comfyui_gets_the_persons_key_and_is_never_charged(tmp_path, monkeypatch):
    import asyncio

    path = tmp_path / "video.api.json"
    path.write_text(json.dumps({"1": {"class_type": "KlingVideoNode", "inputs": {"prompt": "a cat"}}}))
    monkeypatch.setattr(comfy_bridge.studio_state, "checkpoint_answered", lambda _: (True, ""))
    loaders = {"Loader": {"input": {"required": {"vae_name": [["x.safetensors"]]}}},
               "KlingVideoNode": {"input": {"required": {"prompt": ["STRING", {}]}}, "api_node": True}}
    monkeypatch.setattr(comfy_bridge, "_get", lambda *a, **k: Response(status=200, text=json.dumps(loaders)))
    monkeypatch.setattr(comfy_bridge, "_api_node_flags", lambda _: {"KlingVideoNode": True})
    monkeypatch.setattr(comfy_bridge, "_own_connection", lambda: True)
    monkeypatch.setattr(comfy_bridge.studio_state, "run_started", lambda *a: None)
    monkeypatch.setattr(comfy_bridge, "_poll_run", AsyncMock(return_value=None))
    charge, affordable = Mock(), Mock()
    monkeypatch.setattr(comfy_bridge, "_charge", charge)
    monkeypatch.setattr(comfy_bridge, "_affordable", affordable)
    post = Mock(return_value=Response(status=200, text='{"prompt_id":"p1"}'))
    monkeypatch.setattr(comfy_bridge, "_post", post)
    result = await comfy_bridge.ComfyRunTool().execute("t", {"workflow_path": str(path)}, asyncio.Event())
    assert not result.is_error, result.content[0].text
    body = post.call_args.args[1]
    assert body["extra_data"] == {"api_key_comfy_org": "${USER_COMFY_API_KEY}"}
    assert "${COMFY_API_KEY}" not in json.dumps(body)
    charge.assert_not_called()
    affordable.assert_not_called()
