"""A Civitai download link is resolved on the runtime side to Civitai's signed storage URL, with
the platform's key only on refusal; the GPU request and redirect policy accept that form and
nothing else for Civitai; the installation service resolves before a direct download."""

import asyncio
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents/comfy-artchitect/plugins/comfy-bridge"))

import civitai_download_source as civitai  # noqa: E402
from model_download_redirect_policy import ModelDownloadRedirectPolicy  # noqa: E402
from model_download_request import ModelDownloadRequest  # noqa: E402
from model_installation_service import ModelInstallationService  # noqa: E402

LINK = "https://civitai.com/api/download/models/2532617?fileId=2420425"
SIGNED = "https://b2.civitai.com/file/x/woman566.safetensors?X-Amz-Signature=abc&X-Amz-Expires=3600"
FILE = {"filename": "woman566_conan_v1_IL.safetensors", "url": LINK, "kind": "lora"}


class Res(SimpleNamespace):
    @property
    def ok(self):
        return not self.error and 200 <= self.status < 300


def answering(*answers):
    """A fetch that hands out `answers` in order and records every call."""
    calls = []
    queue = list(answers)

    def fetch(url, method="GET", headers=None, timeout_s=30.0, **_):
        calls.append({"url": url, "method": method, "headers": dict(headers or {})})
        return queue.pop(0)

    fetch.calls = calls
    return fetch


def test_a_public_file_is_resolved_with_a_head_and_no_key():
    fetch = answering(Res(status=200, url=SIGNED, error=""))
    out = civitai.resolve(FILE, fetch)
    assert out == {**FILE, "url": SIGNED, "source": "civitai"}
    assert fetch.calls == [{"url": LINK, "method": "HEAD", "headers": {}}]


def test_a_refusal_is_retried_once_with_the_platform_key_placeholder():
    fetch = answering(Res(status=401, url=LINK, error=""), Res(status=200, url=SIGNED, error=""))
    out = civitai.resolve(FILE, fetch)
    assert out["url"] == SIGNED and out["source"] == "civitai"
    assert fetch.calls[1]["headers"] == {"Authorization": "Bearer ${CIVITAI_TOKEN}"}


def test_a_refusal_even_with_the_key_names_the_reason():
    fetch = answering(Res(status=403, url=LINK, error=""), Res(status=403, url=LINK, error=""))
    with pytest.raises(ValueError, match="needs a Civitai API key"):
        civitai.resolve(FILE, fetch)


def test_civitai_may_not_send_the_gpu_off_tls():
    fetch = answering(Res(status=200, url="http://b2.civitai.com/file.safetensors", error=""))
    with pytest.raises(ValueError, match="non-HTTPS"):
        civitai.resolve(FILE, fetch)


@pytest.mark.parametrize("bad", [
    "https://civitai.com/models/2249775/woman",              # the page, not the download
    "https://civitai.com/api/download/models/1?redirect=1",  # a query key the button never sets
    "http://civitai.com/api/download/models/1",              # not TLS
])
def test_only_the_download_endpoint_shape_is_accepted(bad):
    if civitai.is_civitai_download(bad):
        with pytest.raises(ValueError, match="download endpoint"):
            civitai.resolve({**FILE, "url": bad}, answering())
    else:
        assert civitai.resolve({**FILE, "url": bad}, answering()) == {**FILE, "url": bad}


def test_other_links_pass_through_untouched():
    hf = {**FILE, "url": "https://huggingface.co/a/b/resolve/main/woman566_conan_v1_IL.safetensors"}
    fetch = answering()
    assert civitai.resolve(hf, fetch) is hf and fetch.calls == []


def test_the_request_carries_the_source_and_refuses_only_what_is_unsafe():
    ok = ModelDownloadRequest(FILE["filename"], SIGNED, "lora", source="civitai")
    assert ok.directory == "loras" and ok.as_dict()["source"] == "civitai"
    with pytest.raises(ValueError):
        ModelDownloadRequest(FILE["filename"], "http://b2.civitai.com/x", "lora", source="civitai")
    with pytest.raises(ValueError):
        ModelDownloadRequest(FILE["filename"], "https://u:p@b2.civitai.com/x", "lora", source="civitai")


def test_the_gpu_follows_redirects_only_over_plain_https():
    policy = ModelDownloadRedirectPolicy("civitai")
    req = urllib.request.Request(SIGNED)
    assert policy.redirect_request(req, None, 302, "", {}, "https://cdn.anywhere.example/y") is not None
    with pytest.raises(ValueError, match="outside plain HTTPS"):
        policy.redirect_request(req, None, 302, "", {}, "http://cdn.civitai.com/y")
    with pytest.raises(ValueError, match="outside plain HTTPS"):
        policy.redirect_request(req, None, 302, "", {}, "https://u:p@cdn.civitai.com/y")


@pytest.mark.asyncio
async def test_the_service_resolves_a_civitai_link_before_the_gpu_download_starts():
    direct = Mock()
    direct.active.return_value = False
    started = []
    direct.start.side_effect = started.append

    async def wait(requests, abort, report):
        return None

    direct.wait.side_effect = wait
    resolved = {**FILE, "url": SIGNED, "source": "civitai"}
    listed = {}

    async def await_loadable(names, abort):
        return {n: f"loras/{n}" for n in names}

    async def wait_manager(abort, report):
        return "idle"

    service = ModelInstallationService(
        catalog=lambda: [], loadable=lambda: listed, submit=Mock(), start_manager=Mock(),
        manager_busy=lambda: False, queued_recently=lambda f: False, mark_queued=Mock(),
        wait_manager=wait_manager, await_loadable=await_loadable, lease=Mock(), direct=direct,
        resolve_source=lambda file: resolved if file["url"] == LINK else file,
    )
    out = await service.install([FILE], asyncio.Event(), lambda *_: None)
    assert [r.url for r in started] == [SIGNED] and started[0].source == "civitai"
    assert out == {FILE["filename"]: f"loras/{FILE['filename']}"}
