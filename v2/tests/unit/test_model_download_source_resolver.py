"""Provider keys stay on their origin; GPUs receive refreshable file links."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from agent_runtime.infrastructure.net.outbound import Response
from model_download_request import ModelDownloadRequest
from model_download_source_resolver import ModelDownloadSourceResolver
from model_installation_service import ModelInstallationService
from gpu_model_download_failure import GpuModelDownloadFailure


LINKS = [
    ("https://civitai.com/api/download/models/2532617?fileId=2420425", "civitai", "CIVITAI_TOKEN"),
    ("https://huggingface.co/publisher/model/resolve/main/lora.safetensors", "huggingface", "HF_TOKEN"),
]
SIGNED = "https://storage.example/model?signature=temporary"


def file(url):
    return {"filename": "lora.safetensors", "url": url, "kind": "lora"}


@pytest.mark.parametrize("url,provider,secret", LINKS)
def test_public_provider_resolves_without_authentication(url, provider, secret):
    fetch = Mock(return_value=Response(status=200, url=SIGNED))
    resolved = ModelDownloadSourceResolver(fetch=fetch).resolve(file(url))
    assert resolved["url"] == SIGNED and resolved["source"] == provider
    assert resolved["origin_url"] == url
    assert "Authorization" not in fetch.call_args.kwargs["headers"]
    assert fetch.call_args.kwargs["headers"]["User-Agent"] == ModelDownloadRequest.USER_AGENT
    assert fetch.call_args.kwargs["method"] == "HEAD"


@pytest.mark.parametrize("url,provider,secret", LINKS)
@pytest.mark.parametrize("status", [401, 403])
def test_private_provider_uses_only_its_key(url, provider, secret, status):
    fetch = Mock(side_effect=[Response(status=status, url=url), Response(status=200, url=SIGNED)])
    resolved = ModelDownloadSourceResolver(fetch=fetch).resolve(file(url))
    assert fetch.call_args.kwargs["headers"]["Authorization"] == "Bearer ${" + secret + "}"
    assert fetch.call_args.args[0] == url
    assert secret not in str(resolved)
    assert "Authorization" not in resolved


def test_private_huggingface_404_retries_with_key():
    url, _, _ = LINKS[1]
    fetch = Mock(side_effect=[Response(status=404, url=url), Response(status=200, url=SIGNED)])
    assert ModelDownloadSourceResolver(fetch=fetch).resolve(file(url))["url"] == SIGNED
    assert fetch.call_count == 2


@pytest.mark.parametrize("url,provider,secret", LINKS)
def test_storage_403_does_not_claim_provider_auth_failed(url, provider, secret):
    fetch = Mock(return_value=Response(status=403, url=SIGNED))
    with pytest.raises(ValueError, match="does not establish") as error:
        ModelDownloadSourceResolver(fetch=fetch).resolve(file(url))
    assert "storage.example" in str(error.value)
    assert "temporary" not in str(error.value)
    fetch.assert_called_once()


@pytest.mark.parametrize("url,provider,secret", LINKS)
def test_denied_even_with_key_reports_attempt_not_missing_secret(url, provider, secret):
    fetch = Mock(return_value=Response(status=403, url=url))
    with pytest.raises(ValueError, match="runtime tried " + secret):
        ModelDownloadSourceResolver(fetch=fetch).resolve(file(url))


@pytest.mark.parametrize("url", ["https://other.example/weights.safetensors",
                                 "https://civitai.com.other.example/weights.safetensors",
                                 "https://huggingface.co.other.example/weights.safetensors"])
def test_other_public_hosts_do_not_receive_provider_credentials(url):
    fetch = Mock()
    assert ModelDownloadSourceResolver(fetch=fetch).resolve(file(url)) == file(url)
    fetch.assert_not_called()


@pytest.mark.parametrize("url", ["https://civitai.com/models/12/page", "https://civitai.com/api/download/models/12?token=secret",
                                 "https://huggingface.co/org/repo/blob/main/lora.safetensors",
                                 "https://user:pass@huggingface.co/org/repo/resolve/main/lora.safetensors"])
def test_bad_provider_links_are_rejected_before_network(url):
    fetch = Mock()
    with pytest.raises(ValueError):
        ModelDownloadSourceResolver(fetch=fetch).resolve(file(url))
    fetch.assert_not_called()


def test_bad_final_url_and_login_page_are_rejected():
    for response in [Response(status=200, url="http://storage.example/model"),
                     Response(status=200, url="https://user:pass@storage.example/model"),
                     Response(status=200, url=SIGNED, headers={"content-type": "text/html"})]:
        with pytest.raises(ValueError):
            ModelDownloadSourceResolver(fetch=Mock(return_value=response)).resolve(file(LINKS[0][0]))


def test_authenticated_response_must_not_require_sending_key_to_gpu():
    url = LINKS[1][0]
    fetch = Mock(side_effect=[Response(status=401, url=url), Response(status=200, url=url)])
    with pytest.raises(ValueError, match="cannot be sent to the GPU"):
        ModelDownloadSourceResolver(fetch=fetch).resolve(file(url))


def test_rotating_signed_links_keep_same_source_identity():
    url = LINKS[0][0]
    fetch = Mock(side_effect=[Response(status=200, url=SIGNED), Response(status=200, url=SIGNED + "-new")])
    resolver = ModelDownloadSourceResolver(fetch=fetch)
    first, second = [ModelDownloadRequest(**resolver.resolve(file(url))) for _ in range(2)]
    assert first.url != second.url
    assert first.source_id == second.source_id
    assert first.job_id == second.job_id


@pytest.mark.asyncio
@pytest.mark.parametrize("url,provider,secret", LINKS)
async def test_resolved_provider_uses_gpu_even_when_manager_lists_file(url, provider, secret):
    direct = Mock()
    direct.active.return_value = False
    direct.wait = AsyncMock()
    submit = Mock()
    resolver = ModelDownloadSourceResolver(fetch=Mock(return_value=Response(status=200, url=SIGNED)))
    installer = ModelInstallationService(
        catalog=lambda: [file(url)], loadable=lambda: {}, submit=submit,
        start_manager=Mock(), manager_busy=lambda: False, queued_recently=lambda _: False,
        mark_queued=Mock(), wait_manager=AsyncMock(return_value="idle"), lease=Mock(), direct=direct,
        await_loadable=AsyncMock(return_value={"lora.safetensors": "lora.safetensors"}),
        resolve_source=resolver.resolve,
    )
    await installer.install([file(url)], asyncio.Event(), lambda _: None)
    submit.assert_not_called()
    assert direct.start.call_args.args[0].source == provider
    assert direct.start.call_args.args[0].url == SIGNED


@pytest.mark.asyncio
async def test_provider_does_not_duplicate_existing_manager_download():
    url = LINKS[1][0]
    direct = Mock()
    direct.active.return_value = False
    direct.wait = AsyncMock()
    installer = ModelInstallationService(
        catalog=lambda: [file(url)], loadable=lambda: {}, submit=Mock(), start_manager=Mock(),
        manager_busy=lambda: True, queued_recently=lambda _: True, mark_queued=Mock(),
        wait_manager=AsyncMock(return_value="idle"), lease=Mock(), direct=direct,
        await_loadable=AsyncMock(return_value={"lora.safetensors": "lora.safetensors"}),
        resolve_source=ModelDownloadSourceResolver(fetch=Mock(return_value=Response(status=200, url=SIGNED))).resolve,
    )
    await installer.install([file(url)], asyncio.Event(), lambda _: None)
    installer.wait_manager.assert_awaited_once()
    direct.start.assert_not_called()
    installer.submit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["civitai", "huggingface"])
async def test_expired_provider_link_is_refreshed_once(provider):
    url = next(url for url, name, _ in LINKS if name == provider)
    old = ModelDownloadRequest("lora.safetensors", SIGNED, "lora", source=provider, origin_url=url)
    fresh = ModelDownloadRequest("lora.safetensors", SIGNED + "-new", "lora", source=provider, origin_url=url)
    direct = Mock()
    direct.wait = AsyncMock(side_effect=[GpuModelDownloadFailure(old, "Forbidden", 403), None])
    installer = ModelInstallationService(catalog=Mock(), loadable=Mock(), submit=Mock(), start_manager=Mock(),
        manager_busy=Mock(), queued_recently=Mock(), mark_queued=Mock(), wait_manager=Mock(),
        await_loadable=Mock(), lease=Mock(), direct=direct, resolve_source=Mock(return_value=fresh.as_dict()))
    await installer._wait_direct([old], asyncio.Event(), lambda _: None)
    direct.start.assert_called_once_with(fresh)
    assert direct.wait.await_args.args[0] == [fresh]
    installer.resolve_source.assert_called_once_with(file(url))


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["direct", "civitai"])
async def test_refused_download_does_not_loop_forever(source):
    request = ModelDownloadRequest("lora.safetensors", SIGNED, "lora", source=source,
                                   origin_url=LINKS[0][0] if source == "civitai" else "")
    direct = Mock()
    direct.wait = AsyncMock(side_effect=GpuModelDownloadFailure(request, "Forbidden", 403))
    installer = ModelInstallationService(catalog=Mock(), loadable=Mock(), submit=Mock(), start_manager=Mock(),
        manager_busy=Mock(), queued_recently=Mock(), mark_queued=Mock(), wait_manager=Mock(),
        await_loadable=Mock(), lease=Mock(), direct=direct, resolve_source=Mock(return_value=request.as_dict()))
    with pytest.raises(GpuModelDownloadFailure):
        await installer._wait_direct([request], asyncio.Event(), lambda _: None)
    assert direct.start.call_count == (1 if source == "civitai" else 0)
