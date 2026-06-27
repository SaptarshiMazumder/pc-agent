import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.mcp import McpCallResult
from agentd.domain.messages import TextContent
from search import build_search_providers
from search.providers.parallel import ParallelSearchProvider


# ---- fakes -----------------------------------------------------------------

class FakeSession:
    """Structurally satisfies the McpSession bits ParallelSearchProvider uses."""

    def __init__(self, result, closed_log):
        self._result = result
        self.calls = []
        self.closed_log = closed_log

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self._result

    async def close(self):
        self.closed_log.append(True)


def _json_result(obj, is_error=False):
    return McpCallResult(content=[TextContent(text=json.dumps(obj))], is_error=is_error)


def _provider(result):
    closed: list = []
    sess = FakeSession(result, closed)
    state = {"factory_calls": 0}

    async def factory():
        state["factory_calls"] += 1
        return sess

    return ParallelSearchProvider(factory), sess, state


# ---- mapping ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_maps_parallel_results_to_searchresults():
    payload = {"results": [
        {"url": "https://a.com", "title": "A", "publish_date": "2025-01-01",
         "excerpts": ["hello ", " world"]},
        {"url": "https://b.com", "title": "B", "publish_date": None, "excerpts": []},
    ]}
    p, _, _ = _provider(_json_result(payload))
    out = await p.search("recruiters japan", 10, None)
    assert [r.url for r in out] == ["https://a.com", "https://b.com"]
    assert out[0].title == "A" and out[0].age == "2025-01-01"
    assert out[0].snippet == "hello world"           # excerpts joined + trimmed
    assert out[1].snippet == "" and out[1].age == ""  # missing fields -> ""


@pytest.mark.asyncio
async def test_sends_objective_and_search_queries():
    p, sess, _ = _provider(_json_result({"results": []}))
    await p.search("find FDE recruiters", 5, None)
    name, args = sess.calls[0]
    assert name == "web_search"
    assert args["objective"] == "find FDE recruiters"
    assert args["search_queries"] == ["find FDE recruiters"]
    assert isinstance(args["session_id"], str) and len(args["session_id"]) >= 16


@pytest.mark.asyncio
async def test_session_id_reused_across_calls():
    p, sess, _ = _provider(_json_result({"results": []}))
    await p.search("q1", 5, None)
    await p.search("q2", 5, None)
    assert sess.calls[0][1]["session_id"] == sess.calls[1][1]["session_id"]


@pytest.mark.asyncio
async def test_truncates_to_count():
    payload = {"results": [{"url": f"u{i}", "title": str(i), "excerpts": []} for i in range(10)]}
    p, _, _ = _provider(_json_result(payload))
    assert len(await p.search("q", 3, None)) == 3


@pytest.mark.asyncio
async def test_error_result_raises_so_chain_falls_through():
    res = McpCallResult(content=[TextContent(text="rate limited")], is_error=True)
    p, _, _ = _provider(res)
    with pytest.raises(RuntimeError):
        await p.search("q", 5, None)


@pytest.mark.asyncio
async def test_non_json_content_returns_empty():
    res = McpCallResult(content=[TextContent(text="not json at all")], is_error=False)
    p, _, _ = _provider(res)
    assert await p.search("q", 5, None) == []


@pytest.mark.asyncio
async def test_session_is_lazily_connected_once_then_closed():
    p, sess, state = _provider(_json_result({"results": []}))
    assert state["factory_calls"] == 0           # nothing connected until first search
    await p.search("q1", 5, None)
    await p.search("q2", 5, None)
    assert state["factory_calls"] == 1           # session cached + reused
    await p.aclose()
    assert sess.closed_log == [True]


def test_available_true_when_mcp_installed():
    p, _, _ = _provider(_json_result({"results": []}))
    assert p.available() is True  # `mcp` is installed in the test venv


# ---- chain ordering (matches OpenClaw's no-keys default) -------------------

def _search_cfg(**over):
    base = dict(parallel_search_enabled=True, parallel_search_url=None, parallel_api_key=None,
                search_providers=None, search_model="gemini/gemini-2.5-flash",
                model="gemini/x", brave_api_key=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_default_order_is_parallel_then_duckduckgo(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # no gemini key -> parallel is primary
    provs = build_search_providers(_search_cfg())
    assert [p.name for p in provs] == ["parallel", "duckduckgo"]


def test_parallel_disabled_leaves_duckduckgo_only(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provs = build_search_providers(_search_cfg(parallel_search_enabled=False))
    assert [p.name for p in provs] == ["duckduckgo"]


def test_explicit_provider_list_overrides_default():
    provs = build_search_providers(_search_cfg(search_providers=["duckduckgo", "parallel"]))
    assert [p.name for p in provs] == ["duckduckgo", "parallel"]
