import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search.cache import _CACHE
from search.format import format_results
from web_search import WebSearchTool

from agent_runtime.application.interfaces.search import SearchResult


class FakeProvider:
    def __init__(self, name, results=None, error=None, avail=True):
        self.name = name
        self._results = results or []
        self._error = error
        self._avail = avail
        self.called = False

    def available(self):
        return self._avail

    async def search(self, query, count, freshness):
        self.called = True
        if self._error:
            raise self._error
        return self._results


def _hit(name):
    return [SearchResult(title=name, url="https://" + name, snippet="s")]


async def _run(providers, query="q"):
    tool = WebSearchTool(config=None, providers=providers)
    return await tool.execute("t", {"query": query, "count": 5}, asyncio.Event())


@pytest.fixture(autouse=True)
def _clear_cache():
    _CACHE.clear()
    yield
    _CACHE.clear()


@pytest.mark.asyncio
async def test_first_non_empty_wins():
    empty = FakeProvider("empty", results=[])
    good = FakeProvider("good", results=_hit("good"))
    res = await _run([empty, good])
    assert "provider: good" in res.content[0].text
    assert empty.called and good.called


@pytest.mark.asyncio
async def test_error_provider_skipped():
    boom = FakeProvider("boom", error=RuntimeError("x"))
    good = FakeProvider("good", results=_hit("good"))
    res = await _run([boom, good])
    assert not res.is_error and "provider: good" in res.content[0].text


@pytest.mark.asyncio
async def test_all_errors_is_error():
    res = await _run(
        [FakeProvider("a", error=RuntimeError("x")), FakeProvider("b", error=RuntimeError("y"))]
    )
    assert res.is_error and "web_search failed" in res.content[0].text


@pytest.mark.asyncio
async def test_all_empty_is_not_error():
    res = await _run([FakeProvider("a", results=[]), FakeProvider("b", results=[])])
    assert not res.is_error and "no results" in res.content[0].text


@pytest.mark.asyncio
async def test_unavailable_provider_not_called():
    off = FakeProvider("off", results=_hit("off"), avail=False)
    good = FakeProvider("good", results=_hit("good"))
    await _run([off, good])
    assert not off.called and good.called


@pytest.mark.asyncio
async def test_abort_short_circuits():
    tool = WebSearchTool(config=None, providers=[FakeProvider("a", results=_hit("a"))])
    ab = asyncio.Event()
    ab.set()
    res = await tool.execute("t", {"query": "q"}, ab)
    assert res.is_error and "aborted" in res.content[0].text


@pytest.mark.asyncio
async def test_cache_hit_skips_providers():
    good = FakeProvider("good", results=_hit("good"))
    await _run([good], query="cached-q")
    good.called = False
    res = await _run([good], query="cached-q")
    assert not good.called and "provider: cache" in res.content[0].text


def test_format_results_shape():
    out = format_results(
        [SearchResult(title="T", url="https://u", snippet="snip", age="2d")], "brave"
    )
    assert out.startswith("Search results (provider: brave):")
    assert "1. T  (2d)" in out and "https://u" in out and "snip" in out
    assert format_results([], "x") == "No results (provider: x)."
