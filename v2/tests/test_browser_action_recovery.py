"""Action-timeout fail-fast + recovery diagnostics (covered / stale element)."""

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# a button fully covered by a fixed overlay -> clicks can't land on it
COVERED_HTML = (
    "data:text/html,<html><body>"
    "<button id=b onclick=\"document.title='HIT'\">Target</button>"
    "<div id=overlay style='position:fixed;top:0;left:0;width:100%;height:100%;"
    "background:rgba(0,0,0,0.4);z-index:9999'></div>"
    "</body></html>"
)
PLAIN_HTML = "data:text/html,<html><body><button id=b>Target</button></body></html>"


def _cfg(tmp_path):
    return SimpleNamespace(
        state_dir=tmp_path, browser_headless=True, browser_persistent=False,
        browser_downloads=True, browser_console_buffer=50, browser_channel="",
        browser_stealth=False, browser_cursor_scan=False,
        browser_action_timeout_ms=1500,  # short so the test fails fast
    )


async def _provider(tmp_path):
    try:
        from agentd.infrastructure.tools.browser.providers.playwright import PlaywrightBrowserProvider
    except ImportError:
        pytest.skip("playwright not installed")
    mgr = PlaywrightBrowserProvider(_cfg(tmp_path))
    try:
        await mgr.ensure()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot launch chromium: {e}")
    from agentd.infrastructure.tools.browser.tool import BrowserTool
    return mgr, BrowserTool(mgr.config, mgr)


def _ref(text):
    m = re.search(r'button "Target" \[ref=(e\d+)\]', text)
    assert m, f"no button ref in:\n{text}"
    return m.group(1)


@pytest.mark.asyncio
async def test_covered_element_fails_fast_with_diagnostic(tmp_path):
    mgr, tool = await _provider(tmp_path)
    res = await tool.execute("c", {"action": "navigate", "url": COVERED_HTML}, asyncio.Event())
    ref = _ref(res.content[0].text)
    # click is blocked by the overlay -> should fail within ~1.5s (not 30s) with a hint
    import time
    t0 = time.monotonic()
    res = await tool.execute("c", {"action": "act", "kind": "click", "ref": ref}, asyncio.Event())
    elapsed = time.monotonic() - t0
    txt = res.content[0].text
    assert res.is_error
    assert "covered by" in txt and "overlay" in txt, txt
    assert elapsed < 10, f"should fail fast, took {elapsed:.1f}s"
    await mgr.close()


@pytest.mark.asyncio
async def test_stale_ref_reports_stale(tmp_path):
    mgr, tool = await _provider(tmp_path)
    res = await tool.execute("c", {"action": "navigate", "url": PLAIN_HTML}, asyncio.Event())
    ref = _ref(res.content[0].text)
    # remove the element, then click the now-stale ref
    await tool.execute("c", {"action": "act", "kind": "evaluate",
                             "expression": "document.getElementById('b').remove()"}, asyncio.Event())
    res = await tool.execute("c", {"action": "act", "kind": "click", "ref": ref}, asyncio.Event())
    txt = res.content[0].text
    assert res.is_error and "stale" in txt, txt
    await mgr.close()
