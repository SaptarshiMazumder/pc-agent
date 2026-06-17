import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.tools.search.factory import build_search_providers


def _cfg(model="gemini/gemini-2.5-pro", brave=None, search_providers=None):
    return SimpleNamespace(model=model, brave_api_key=brave, search_providers=search_providers)


def _names(cfg):
    return [p.name for p in build_search_providers(cfg)]


def test_default_gemini_first_with_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert _names(_cfg(brave="bk")) == ["gemini", "brave", "duckduckgo"]


def test_default_no_brave(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert _names(_cfg(brave=None)) == ["gemini", "duckduckgo"]


def test_default_non_gemini_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert _names(_cfg(model="openai/gpt-5.5", brave=None)) == ["duckduckgo"]


def test_default_no_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert _names(_cfg(brave="bk")) == ["brave", "duckduckgo"]


def test_explicit_override_respected_and_unknowns_dropped(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    names = _names(_cfg(search_providers=["brave", "tavily", "gemini"]))
    assert names == ["brave", "gemini"]  # tavily unknown -> dropped, order preserved
