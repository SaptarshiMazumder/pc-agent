import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search.providers.brave import BraveProvider
from search.providers.duckduckgo import DuckDuckGoProvider
from search.providers.gemini import GeminiGroundingProvider

from agent_runtime.application.interfaces.search import SearchProvider, SearchResult


def test_providers_satisfy_port():
    assert isinstance(BraveProvider("k"), SearchProvider)
    assert isinstance(DuckDuckGoProvider(), SearchProvider)
    assert isinstance(GeminiGroundingProvider("gemini/x", "k"), SearchProvider)


# ---- Brave ------------------------------------------------------------


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeClient:
    captured: dict = {}

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        _FakeClient.captured = {"params": params, "headers": headers}
        return _FakeResp(
            {
                "web": {
                    "results": [
                        {"title": "T1", "url": "https://a", "description": "D1", "age": "1 day"},
                    ]
                }
            }
        )


@pytest.mark.asyncio
async def test_brave_parse_and_freshness(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    p = BraveProvider("secret")
    out = await p.search("hi", 5, "week")
    assert out == [SearchResult(title="T1", url="https://a", snippet="D1", age="1 day")]
    assert _FakeClient.captured["params"]["freshness"] == "pw"  # week -> pw
    assert _FakeClient.captured["headers"]["X-Subscription-Token"] == "secret"
    assert p.available() and not BraveProvider("").available()


# ---- DuckDuckGo -------------------------------------------------------


@pytest.mark.asyncio
async def test_ddg_wraps_sync_and_ignores_freshness(monkeypatch):
    sentinel = [SearchResult(title="d", url="https://d", snippet="s")]
    monkeypatch.setattr(DuckDuckGoProvider, "_sync", staticmethod(lambda q, c: sentinel))
    out = await DuckDuckGoProvider().search("q", 3, "day")  # freshness ignored
    assert out == sentinel


# ---- Gemini grounding (parse only; no network) ------------------------


class _FakeHeadResp:
    def __init__(self, url):
        self.url = url


class _FakeGeminiClient:
    """Resolves a redirect by rewriting 'redirect://x' -> 'https://real/x', so the
    test can assert the URL was actually resolved (OpenClaw's redirect step)."""

    async def head(self, url, follow_redirects=True, timeout=None):
        return _FakeHeadResp(url.replace("redirect://", "https://real/"))


def _gen_data(answer, chunks):
    # Raw Gemini generateContent JSON shape.
    cand = {"content": {"parts": [{"text": answer}] if answer else []}}
    if chunks is not None:
        cand["groundingMetadata"] = {"groundingChunks": chunks}
    return {"candidates": [cand]}


@pytest.mark.asyncio
async def test_gemini_parse_answer_plus_resolved_citations():
    chunks = [
        {"web": {"uri": "redirect://x", "title": "X"}},
        {"web": {"uri": "redirect://y", "title": "Y"}},
        {"web": {"uri": "redirect://z", "title": "Z"}},
    ]
    p = GeminiGroundingProvider("gemini/x", "k")
    out = await p._parse(_FakeGeminiClient(), _gen_data("the answer", chunks), count=2)
    assert out[0] == SearchResult(title="Gemini grounded answer", url="", snippet="the answer")
    # redirect URLs resolved + citations truncated to count=2
    assert [r.url for r in out[1:]] == ["https://real/x", "https://real/y"]
    assert out[1].title == "X"


@pytest.mark.asyncio
async def test_gemini_returns_answer_even_without_chunks():
    # OpenClaw-parity fix: the synthesized answer is returned even with ZERO grounding
    # chunks (the old code dropped it and fell through to DuckDuckGo).
    p = GeminiGroundingProvider("gemini/x", "k")
    out = await p._parse(_FakeGeminiClient(), _gen_data("ungrounded answer", []), count=5)
    assert out == [
        SearchResult(title="Gemini grounded answer", url="", snippet="ungrounded answer")
    ]


@pytest.mark.asyncio
async def test_gemini_no_candidates_returns_empty():
    p = GeminiGroundingProvider("gemini/x", "k")
    assert await p._parse(_FakeGeminiClient(), {"candidates": []}, count=5) == []


def test_gemini_available(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert GeminiGroundingProvider("gemini/gemini-2.5-pro").available()
    assert not GeminiGroundingProvider("openai/gpt-5.5").available()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not GeminiGroundingProvider("gemini/gemini-2.5-pro").available()
