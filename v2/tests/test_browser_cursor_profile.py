"""Tests for the two agent-browser-inspired features:
- cursor-interactive scan (ref non-ARIA clickables) — real headless browser
- Chrome-profile seeding (login reuse) — temp dirs, no real Chrome needed
"""

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CURSOR_HTML = (
    "data:text/html,<html><body>"
    "<div id=fake onclick=\"document.title='DIVCLICK'\" style='cursor:pointer'>Fake Button</div>"
    "<span tabindex='0'>Focusable Span</span>"
    "<p>just text</p>"
    "</body></html>"
)


def _cfg(tmp_path, **over):
    base = dict(state_dir=tmp_path, browser_headless=True, browser_persistent=False,
               browser_downloads=True, browser_console_buffer=50,
               browser_channel="", browser_stealth=False, browser_cursor_scan=True)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_cursor_scan_refs_and_clicks_non_aria_div(tmp_path):
    try:
        from agentd.infrastructure.tools.browser.providers.playwright import PlaywrightBrowserProvider
    except ImportError:
        pytest.skip("playwright not installed")
    from browser_tool import BrowserTool

    mgr = PlaywrightBrowserProvider(_cfg(tmp_path))
    try:
        await mgr.ensure()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot launch chromium: {e}")
    tool = BrowserTool(mgr.config, mgr)

    res = await tool.execute("c", {"action": "navigate", "url": CURSOR_HTML}, asyncio.Event())
    text = res.content[0].text
    assert "[non-ARIA clickables" in text, text
    assert "Fake Button" in text
    m = re.search(r'clickable "Fake Button" <div> \[ref=(c\d+)\]', text)
    assert m, f"no cursor ref for the clickable div:\n{text}"
    cref = m.group(1)

    # the focusable span is also picked up
    assert "Focusable Span" in text

    # click the non-ARIA div by its cursor ref -> its onclick fires
    await tool.execute("c", {"action": "act", "kind": "click", "ref": cref}, asyncio.Event())
    res = await tool.execute("c", {"action": "act", "kind": "evaluate",
                                   "expression": "document.title"}, asyncio.Event())
    assert "DIVCLICK" in res.content[0].text

    # disabling the scan removes the section
    mgr.config.browser_cursor_scan = False
    res = await tool.execute("c", {"action": "snapshot"}, asyncio.Event())
    assert "[non-ARIA clickables" not in res.content[0].text

    await mgr.close()


def test_resolve_chrome_profile_by_abs_path(tmp_path):
    from agentd.infrastructure.tools.browser.providers.playwright import resolve_chrome_profile
    prof = tmp_path / "SomeProfile"
    prof.mkdir()
    assert resolve_chrome_profile(str(prof)) == prof
    assert resolve_chrome_profile(str(tmp_path / "missing")) is None


def test_seed_profile_copies_excluding_caches_and_is_idempotent(tmp_path):
    from agentd.infrastructure.tools.browser.providers.playwright import seed_profile_from_chrome

    # fake Chrome layout: <UserData>/Default + <UserData>/Local State
    user_data = tmp_path / "UserData"
    src = user_data / "Default"
    (src / "Cache").mkdir(parents=True)
    (src / "Cache" / "junk.bin").write_bytes(b"x" * 100)
    (src / "Cookies").write_bytes(b"cookiedata")
    (src / "Login Data").write_bytes(b"logins")
    (user_data / "Local State").write_text('{"profile":{"info_cache":{}}}', encoding="utf-8")

    target = tmp_path / "imported"
    assert seed_profile_from_chrome(str(src), target) is True
    assert (target / "Default" / "Cookies").read_bytes() == b"cookiedata"
    assert (target / "Default" / "Login Data").exists()
    assert not (target / "Default" / "Cache").exists()   # caches excluded
    assert (target / "Local State").exists()             # cookie-key file copied

    # idempotent: already seeded -> returns True, doesn't error
    assert seed_profile_from_chrome(str(src), target) is True


def test_seed_profile_missing_source_returns_false(tmp_path):
    from agentd.infrastructure.tools.browser.providers.playwright import seed_profile_from_chrome
    assert seed_profile_from_chrome(str(tmp_path / "nope"), tmp_path / "out") is False
