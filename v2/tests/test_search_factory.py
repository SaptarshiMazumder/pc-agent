import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from search.factory import build_search_providers


def _cfg(model="gemini/gemini-2.5-pro", brave=None, plugins=None):
    return SimpleNamespace(model=model, brave_api_key=brave, plugins=plugins or {})


def _names(cfg):
    return [p.name for p in build_search_providers(cfg)]


def test_default_gemini_first_when_key_present(monkeypatch):
    # Mirrors OpenClaw: gemini reuses the model key, so it's primary (20) ahead of
    # parallel (76) and duckduckgo (100).
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert _names(_cfg()) == ["gemini", "parallel", "duckduckgo"]


def test_default_parallel_first_without_gemini_key(monkeypatch):
    # No Gemini key -> Parallel's keyless Search MCP becomes the primary, like OpenClaw.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert _names(_cfg()) == ["parallel", "duckduckgo"]


def test_parallel_disabled_leaves_duckduckgo(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = _cfg()
    cfg.parallel_search_enabled = False
    assert _names(cfg) == ["duckduckgo"]


def test_explicit_override_respected_and_unknowns_dropped(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    names = _names(
        _cfg(
            plugins={"web": {"tools": {"web_search": {"provider": ["brave", "tavily", "gemini"]}}}}
        )
    )
    assert names == ["brave", "gemini"]  # tavily unknown -> dropped, order preserved
