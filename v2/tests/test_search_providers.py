import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.interfaces.search import SearchProvider, SearchResult
from agentd.infrastructure.tools.search.providers.brave import BraveProvider
from agentd.infrastructure.tools.search.providers.duckduckgo import DuckDuckGoProvider
from agentd.infrastructure.tools.search.providers.gemini import GeminiGroundingProvider


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
        return _FakeResp({"web": {"results": [
            {"title": "T1", "url": "https://a", "description": "D1", "age": "1 day"},
        ]}})


@pytest.mark.asyncio
async def test_brave_parse_and_freshness(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    p = BraveProvider("secret")
    out = await p.search("hi", 5, "week")
    assert out == [SearchResult(title="T1", url="https://a", snippet="D1", age="1 day")]
    assert _FakeClient.captured["params"]["freshness"] == "pw"      # week -> pw
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

def _fake_gemini_resp(answer, chunks):
    msg = SimpleNamespace(content=answer)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(
        choices=[choice],
        vertex_ai_grounding_metadata=[{"groundingChunks": chunks}],
    )


def test_gemini_parse_answer_plus_citations():
    chunks = [
        {"web": {"uri": "https://x", "title": "X"}},
        {"web": {"uri": "https://y", "title": "Y"}},
        {"web": {"uri": "https://z", "title": "Z"}},
    ]
    resp = _fake_gemini_resp("the answer", chunks)
    out = GeminiGroundingProvider._parse(resp, count=2)
    assert out[0] == SearchResult(title="Gemini grounded answer", url="", snippet="the answer")
    # citations truncated to count=2
    assert [r.url for r in out[1:]] == ["https://x", "https://y"]


def test_gemini_parse_no_grounding_returns_empty():
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ungrounded"))],
        vertex_ai_grounding_metadata=None,
    )
    assert GeminiGroundingProvider._parse(resp, count=5) == []


def test_gemini_available(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert GeminiGroundingProvider("gemini/gemini-2.5-pro").available()
    assert not GeminiGroundingProvider("openai/gpt-5.5").available()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not GeminiGroundingProvider("gemini/gemini-2.5-pro").available()
