import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.interfaces.fetch import FetchProvider, FetchResult
from fetch.extract import extract_html, sanitize_url, truncate
from fetch.factory import build_fetch_providers
from web_fetch import _CACHE, MIN_USEFUL_CHARS, WebFetchTool

LONG = "x" * (MIN_USEFUL_CHARS + 50)
THIN = "tiny"


class FakeFetch:
    def __init__(self, name, text="", error=None, avail=True):
        self.name = name
        self._text = text
        self._error = error
        self._avail = avail
        self.called = False

    def available(self):
        return self._avail

    async def fetch(self, url, max_chars):
        self.called = True
        if self._error:
            raise self._error
        return FetchResult(url=url, final_url=url, status=200, title="T", text=self._text)


async def _run(providers, url="https://ex.com"):
    return await WebFetchTool(config=None, providers=providers).execute(
        "t", {"url": url}, asyncio.Event()
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    _CACHE.clear()
    yield
    _CACHE.clear()


# ---- extract helpers --------------------------------------------------

def test_sanitize_url():
    assert sanitize_url("https:// example.com ") == "https://example.com"
    with pytest.raises(ValueError):
        sanitize_url("ftp://nope")


def test_truncate():
    text, tr = truncate("abcdef", 3)
    assert tr and text.startswith("abc")
    assert truncate("ab", 10) == ("ab", False)


def test_extract_html_basic():
    title, text = extract_html("<html><head><title>Hi</title></head><body><p>Hello world</p></body></html>", "https://x")
    assert "Hello world" in text


# ---- dispatcher: escalation chain ------------------------------------

@pytest.mark.asyncio
async def test_httpx_good_no_escalation():
    httpx = FakeFetch("httpx", text=LONG)
    browser = FakeFetch("browser_render", text=LONG)
    res = await _run([httpx, browser])
    assert not res.is_error and httpx.called and not browser.called


@pytest.mark.asyncio
async def test_thin_httpx_escalates_to_browser():
    httpx = FakeFetch("httpx", text=THIN)
    browser = FakeFetch("browser_render", text=LONG)
    res = await _run([httpx, browser])
    assert browser.called and LONG in res.content[0].text


@pytest.mark.asyncio
async def test_httpx_error_escalates():
    httpx = FakeFetch("httpx", error=RuntimeError("getaddrinfo failed"))
    browser = FakeFetch("browser_render", text=LONG)
    res = await _run([httpx, browser])
    assert not res.is_error and browser.called and LONG in res.content[0].text


@pytest.mark.asyncio
async def test_all_thin_returns_longest():
    short = FakeFetch("httpx", text="ab")
    longer = FakeFetch("browser_render", text="abcd")
    res = await _run([short, longer])
    assert not res.is_error and "abcd" in res.content[0].text


@pytest.mark.asyncio
async def test_all_fail_is_error():
    res = await _run([FakeFetch("httpx", error=RuntimeError("x"))])
    assert res.is_error and "web_fetch failed" in res.content[0].text


@pytest.mark.asyncio
async def test_cache_hit():
    httpx = FakeFetch("httpx", text=LONG)
    await _run([httpx], url="https://cached.example")
    httpx.called = False
    await _run([httpx], url="https://cached.example")
    assert not httpx.called


# ---- factory ----------------------------------------------------------

def test_factory_chain():
    assert [p.name for p in build_fetch_providers(None, None)] == ["httpx"]
    assert [p.name for p in build_fetch_providers(None, object())] == ["httpx", "browser_render"]
    assert all(isinstance(p, FetchProvider) for p in build_fetch_providers(None, object()))
