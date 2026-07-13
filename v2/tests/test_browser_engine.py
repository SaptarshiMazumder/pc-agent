import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.config import resolve_browser_engine


def _cfg(provider=None):
    plugins = {"browser": {"tools": {"browser": {"provider": provider}}}} if provider else {}
    return SimpleNamespace(plugins=plugins)


def test_default_uses_ours():
    # no provider set => our built-in playwright engine
    assert resolve_browser_engine(_cfg()) == "playwright"


def test_explicit_playwright():
    assert resolve_browser_engine(_cfg("playwright")) == "playwright"


def test_agent_browser_selected():
    assert resolve_browser_engine(_cfg("agent_browser")) == "agent_browser"


def test_unknown_provider_falls_back_to_ours():
    # never lose the browser: anything but "agent_browser" => playwright
    assert resolve_browser_engine(_cfg("nonsense")) == "playwright"


def test_missing_plugins_defaults_to_ours():
    assert resolve_browser_engine(SimpleNamespace()) == "playwright"
