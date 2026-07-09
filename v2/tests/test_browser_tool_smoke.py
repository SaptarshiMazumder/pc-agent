"""End-to-end smoke test for the browser tool against a REAL headless Chromium.

Exercises every capability we ship for OpenClaw parity: durable aria refs,
enriched act (click/type/fill-fields/select/double-click), screenshot variants,
stable tab handles, console, pdf, dialog, upload, status/doctor/profiles.

Skips cleanly if Playwright/Chromium isn't installed.
"""

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = [pytest.mark.asyncio, pytest.mark.browser]

HTML = (
    "data:text/html,"
    "<html><body>"
    "<h1>Smoke</h1>"
    "<button id=b onclick=\"document.getElementById('out').textContent='clicked'\">Click Me</button>"
    "<div id=out></div>"
    "<input id=name placeholder=name>"
    "<input id=email placeholder=email>"
    "<a href='https://example.com/page'>Example Link</a>"
    "<input id=file type=file>"
    "<select id=sel><option value=a>A</option><option value=b>B</option></select>"
    "<button id=al onclick=\"alert('hello')\">Alert</button>"
    "<iframe id=f srcdoc=\"<button onclick=&quot;document.title='FRAMED'&quot;>InFrame</button>\"></iframe>"
    "<script>console.log('SMOKE_CONSOLE_MARKER')</script>"
    "</body></html>"
)

_REF_RE = re.compile(r'-\s+(\w+)\s+"([^"]*)"\s+\[ref=(e\d+)\]')


def _refs(snapshot_text: str) -> dict:
    return {(role, name): ref for role, name, ref in _REF_RE.findall(snapshot_text)}


def _provider(tmp_path):
    try:
        from agentd.infrastructure.tools.browser.providers.playwright import (
            PlaywrightBrowserProvider,
        )
    except ImportError:
        pytest.skip("playwright not installed")
    cfg = SimpleNamespace(
        state_dir=tmp_path,
        browser_headless=True,
        browser_persistent=False,  # ephemeral: fast, no profile lock
        browser_downloads=True,
        browser_console_buffer=200,
        browser_channel="",  # bundled Chromium — hermetic, no real-Chrome dependency
        browser_stealth=False,
    )
    return PlaywrightBrowserProvider(cfg), cfg


async def _run(tool, **params):
    return await tool.execute("call", params, asyncio.Event())


async def test_browser_tool_full_smoke(tmp_path):
    from browser_tool import BrowserTool

    mgr, cfg = _provider(tmp_path)
    try:
        await mgr.ensure()
    except Exception as e:  # chromium not downloaded etc.
        pytest.skip(f"cannot launch chromium: {e}")
    tool = BrowserTool(cfg, mgr)

    # --- navigate + durable aria refs ----------------------------------------
    res = await _run(tool, action="navigate", url=HTML)
    text = res.content[0].text
    assert not res.is_error, text
    assert "[ref=e" in text, "snapshot should carry durable aria refs"
    assert 'button "Click Me"' in text

    # --- snapshot with urls --------------------------------------------------
    res = await _run(tool, action="snapshot", urls=True)
    assert "https://example.com/page" in res.content[0].text

    refs = _refs(res.content[0].text)
    btn = refs[("button", "Click Me")]

    # --- click by ref --------------------------------------------------------
    await _run(tool, action="act", kind="click", ref=btn)
    res = await _run(
        tool, action="act", kind="evaluate", expression="document.getElementById('out').textContent"
    )
    assert "clicked" in res.content[0].text

    # --- multi-field fill in ONE act (selector-based) ------------------------
    await _run(
        tool,
        action="act",
        kind="fill",
        fields=[
            {"selector": "#name", "text": "Alice"},
            {"selector": "#email", "text": "alice@example.com"},
        ],
    )
    res = await _run(
        tool,
        action="act",
        kind="evaluate",
        expression="document.getElementById('name').value + '|' + document.getElementById('email').value",
    )
    assert "Alice|alice@example.com" in res.content[0].text

    # --- type by ref (slowly) ------------------------------------------------
    res = await _run(tool, action="snapshot")
    refs = _refs(res.content[0].text)
    name_ref = next(r for (role, _), r in refs.items() if role == "textbox")
    await _run(tool, action="act", kind="type", ref=name_ref, text="Bob", slowly=True)
    res = await _run(
        tool, action="act", kind="evaluate", expression="document.getElementById('name').value"
    )
    assert "Bob" in res.content[0].text

    # --- select option -------------------------------------------------------
    res = await _run(tool, action="snapshot")
    refs = _refs(res.content[0].text)
    sel_ref = next((r for (role, _), r in refs.items() if role == "combobox"), None)
    if sel_ref:
        await _run(tool, action="act", kind="select", ref=sel_ref, value="b")
        res = await _run(
            tool, action="act", kind="evaluate", expression="document.getElementById('sel').value"
        )
        assert "b" in res.content[0].text

    # --- screenshots: full page + single element -----------------------------
    res = await _run(tool, action="screenshot", full_page=True)
    assert "Screenshot saved" in res.content[0].text
    assert Path(res.content[0].text.split("Screenshot saved: ")[1].strip()).exists()
    res = await _run(tool, action="screenshot", element="#b", image_type="jpeg")
    assert ".jpeg" in res.content[0].text

    # --- stable tab handles: open(labelled) / list / focus / close -----------
    res = await _run(tool, action="open", url=HTML, label="second")
    assert "[tab second]" in res.content[0].text
    res = await _run(tool, action="tabs")
    assert "second" in res.content[0].text and res.details["tabs"]
    res = await _run(tool, action="focus", target_id="second")
    assert not res.is_error
    res = await _run(tool, action="close", target_id="second")
    assert "Closed tab" in res.content[0].text

    # --- console capture -----------------------------------------------------
    res = await _run(tool, action="console")
    assert "SMOKE_CONSOLE_MARKER" in res.content[0].text

    # --- pdf (headless chromium) ---------------------------------------------
    res = await _run(tool, action="pdf")
    assert "PDF saved" in res.content[0].text and not res.is_error

    # --- dialog auto-accept (no hang) ----------------------------------------
    res = await _run(tool, action="snapshot")
    refs = _refs(res.content[0].text)
    al = refs[("button", "Alert")]
    await _run(tool, action="act", kind="click", ref=al)
    await asyncio.sleep(0.1)
    assert mgr._last_dialog and mgr._last_dialog["message"] == "hello"

    # --- upload --------------------------------------------------------------
    f = tmp_path / "upload.txt"
    f.write_text("hi")
    await _run(tool, action="upload", selector="#file", paths=[str(f)])
    res = await _run(
        tool,
        action="act",
        kind="evaluate",
        expression="document.getElementById('file').files.length",
    )
    assert "1" in res.content[0].text

    # --- iframe: full snapshot descends in; frame-encoded ref resolves -------
    res = await _run(tool, action="snapshot")
    fref = re.search(r'button "InFrame" \[ref=([a-z0-9]+)\]', res.content[0].text)
    assert fref, "iframe contents should appear in the full snapshot"
    r = await _run(tool, action="act", kind="click", ref=fref.group(1))
    assert not r.is_error, r.content[0].text

    # --- status / doctor / profiles ------------------------------------------
    res = await _run(tool, action="status")
    assert res.details["running"] is True and res.details["mode"] == "ephemeral"
    res = await _run(tool, action="doctor")
    assert res.details["ok"] is True
    res = await _run(tool, action="profiles")
    assert "available" in res.details

    await mgr.close()
