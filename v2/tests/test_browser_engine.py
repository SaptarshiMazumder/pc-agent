import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.config import resolve_browser_engine


def _cfg(pw, ab):
    return SimpleNamespace(browser_engine_playwright=pw, browser_engine_agent_browser=ab)


def test_default_uses_ours():
    # default: ours on, agent-browser off
    assert resolve_browser_engine(_cfg(True, False)) == "playwright"


def test_both_on_uses_ours():
    # both on => ours wins
    assert resolve_browser_engine(_cfg(True, True)) == "playwright"


def test_ours_off_theirs_on_uses_agent_browser():
    assert resolve_browser_engine(_cfg(False, True)) == "agent_browser"


def test_both_off_falls_back_to_ours():
    # never lose the browser
    assert resolve_browser_engine(_cfg(False, False)) == "playwright"


def test_missing_attrs_default_to_ours():
    assert resolve_browser_engine(SimpleNamespace()) == "playwright"
