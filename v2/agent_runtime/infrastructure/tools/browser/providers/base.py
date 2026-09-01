"""BaseBrowserSession — driver-agnostic browser session logic.

All the behaviour the `browser` tool relies on lives here and operates on a
Playwright `context`: stable tab handles (tabId + label), per-tab console
buffers, dialog handling, download capture, the native AI aria-snapshot, and
aria-ref resolution. Concrete providers only implement `_create_context()`
(how the context is obtained): launch our own (Playwright) or attach to a
running browser (CDP). Everything else is shared.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from pathlib import Path

from agent_runtime.application.tool_models import browser_knob
from agent_runtime.infrastructure.tools.browser.snapshot import (
    _NETWORKIDLE_TIMEOUT_MS,
    DEFAULT_AI_SNAPSHOT_MAX_CHARS,
    format_ai_snapshot,
)

log = logging.getLogger("agentd")

# Cursor-interactive scan (ported from agent-browser's find_cursor_interactive_elements):
# find elements the ARIA tree misses — clickable <div>s, onclick handlers, focusable
# (tabindex), contenteditable — and tag each with data-agentd-ci="cN" so it can be
# resolved like any ref. Skips native/already-ARIA-interactive elements, hidden/zero-size
# ones, and child elements that only inherit cursor:pointer from a tagged parent.
_CURSOR_SCAN_JS = r"""
() => {
  const CAP = 80;
  const NATIVE = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','OPTION','SUMMARY','LABEL','AREA']);
  const IROLES = new Set(['button','link','checkbox','radio','menuitem','menuitemcheckbox',
    'menuitemradio','tab','switch','option','textbox','combobox','slider','spinbutton',
    'searchbox','treeitem']);
  document.querySelectorAll('[data-agentd-ci]').forEach(e => e.removeAttribute('data-agentd-ci'));
  const out = [];
  let n = 0;
  const all = document.body ? document.body.querySelectorAll('*') : [];
  for (const el of all) {
    if (n >= CAP) break;
    if (NATIVE.has(el.tagName)) continue;
    const role = el.getAttribute('role');
    if (role && IROLES.has(role)) continue;          // already in the ARIA tree
    if (el.closest('[aria-hidden="true"],[hidden]')) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const onclick = el.hasAttribute('onclick');
    const ti = el.getAttribute('tabindex');
    const focusable = ti !== null && ti !== '-1';
    const editable = el.isContentEditable;
    const pointer = cs.cursor === 'pointer';
    if (!(onclick || focusable || editable || pointer)) continue;
    if (pointer && !onclick && !focusable && !editable) {
      const p = el.parentElement;                    // dedupe inherited cursor:pointer
      if (p && getComputedStyle(p).cursor === 'pointer') continue;
    }
    n += 1;
    const ref = 'c' + n;
    el.setAttribute('data-agentd-ci', ref);
    let t = (el.innerText || el.getAttribute('aria-label') || el.title || '').trim();
    t = t.replace(/\s+/g, ' ').slice(0, 80);
    out.push({ref, tag: el.tagName.toLowerCase(), text: t,
              box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]});
  }
  return out;
}
"""


class BaseBrowserSession:
    """Owns the Playwright context + tab/listener bookkeeping; lives on the loop."""

    mode = "base"

    def __init__(self, config):
        self.config = config
        self._pw = None
        self._browser = None  # set by adapters that launch/connect a Browser object
        self.context = None
        self.active_page = None
        # tab registry — stable handles that survive index shuffling
        self._ids: dict = {}  # page -> "t1"
        self._labels: dict = {}  # label -> page
        self._counter = 0
        # per-page state
        self._console: dict = {}  # page -> deque[str]
        self._downloads: list = []  # [{filename, path, url}]
        self._last_dialog: dict | None = None
        self._dialog_accept = True  # auto-accept by default so nothing hangs
        self._dialog_prompt: str | None = None

    # ----------------------------------------------------------- lifecycle
    async def _create_context(self):
        """Return (playwright, context). Adapters implement this."""
        raise NotImplementedError

    async def ensure(self):
        # SELF-HEAL: only reuse the session if the browser is still ALIVE. A closed/crashed
        # browser (e.g. the headed window was closed) leaves a stale, non-null `context`; without
        # this liveness check every later `new_page()` raises TargetClosedError and the whole
        # (shared) browser wedges until the gateway restarts. A dead handle -> tear down + relaunch.
        if self.context is not None and self._connected():
            return
        await self._discard()  # clear any dead/stale handle first
        self._pw, self.context = await self._create_context()
        try:
            # cap per-action waits (Playwright defaults to 30s) so a covered/stale
            # element fails fast and the agent can recover instead of hanging.
            self.context.set_default_timeout(browser_knob(self.config, "action_timeout_ms", 12000))
        except Exception:
            pass
        try:
            self.context.on("page", self._track_page)
        except Exception:
            pass
        try:
            # if the browser/context dies under us, drop the handle so the NEXT ensure relaunches.
            self.context.once("close", self._on_session_closed)
        except Exception:
            pass
        for page in list(self.context.pages):
            self._track_page(page)
        if not self.context.pages:
            await self.context.new_page()  # fires _track_page
        self.active_page = self.context.pages[0]

    def _connected(self) -> bool:
        """Whether the launched browser is still alive (so ``context.new_page()`` will work)."""
        br = self._browser
        if br is None:
            return self.context is not None  # providers that don't expose a Browser
        try:
            return bool(br.is_connected())
        except Exception:
            return False

    def _on_session_closed(self, *_):
        """The browser/context closed out from under us (window closed, crash) — drop the stale
        handles so the next ``ensure()`` relaunches instead of using a dead context."""
        self.context = None
        self.active_page = None

    async def _discard(self):
        """Tear down a dead/stale session before relaunching. Best-effort; never raises."""
        try:
            await self.close()
        except Exception:
            pass

    async def close(self):
        for obj in (self.context, self._browser, self._pw):
            if obj is None:
                continue
            try:
                stop = getattr(obj, "stop", None) or getattr(obj, "close", None)
                if stop:
                    res = stop()
                    if asyncio.iscoroutine(res):
                        await res
            except Exception:
                pass
        self._pw = self._browser = self.context = self.active_page = None
        self._ids.clear()
        self._labels.clear()
        self._console.clear()

    async def settle(self):
        """Best-effort wait for the network to go idle after nav/interaction."""
        try:
            await self.active_page.wait_for_load_state(
                "networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS
            )
        except Exception:
            pass

    # ----------------------------------------------------------- tab registry
    def _track_page(self, page):
        if page in self._ids:
            return
        self._counter += 1
        self._ids[page] = f"t{self._counter}"
        self._console[page] = deque(maxlen=browser_knob(self.config, "console_buffer", 200))

        page.on("console", lambda msg, p=page: self._on_console(p, msg))
        page.on("dialog", self._on_dialog)
        page.on("download", self._on_download)

        def _forget(p=page):
            self._ids.pop(p, None)
            self._console.pop(p, None)
            for lbl, pg in list(self._labels.items()):
                if pg is p:
                    self._labels.pop(lbl, None)

        page.on("close", lambda *_: _forget())

    def _label_page(self, page, label: str | None):
        if label:
            self._labels[label] = page

    def tab_handle(self, page) -> str:
        for lbl, pg in self._labels.items():
            if pg is page:
                return lbl
        return self._ids.get(page, "?")

    def suggested_target_id(self, page) -> str:
        return self.tab_handle(page)

    def resolve_target(self, target_id):
        """label | tabId (t3) | numeric index (compat) | None -> the active page."""
        if not target_id:
            return self.active_page
        target_id = str(target_id)
        if target_id in self._labels:
            return self._labels[target_id]
        for page, tid in self._ids.items():
            if tid == target_id:
                return page
        if target_id.isdigit():
            pages = self.context.pages
            idx = int(target_id)
            if idx < len(pages):
                return pages[idx]
        # raw prefix match on tabId
        for page, tid in self._ids.items():
            if tid.startswith(target_id):
                return page
        raise ValueError(
            f"Unknown tab '{target_id}'. Use a label, tabId (e.g. t2), or run action=tabs to list."
        )

    def list_tabs(self) -> list[dict]:
        rows = []
        for page in self.context.pages:
            rows.append(
                {
                    "tabId": self._ids.get(page, "?"),
                    "label": next((l for l, p in self._labels.items() if p is page), None),
                    "suggestedTargetId": self.suggested_target_id(page),
                    "url": page.url,
                    "active": page is self.active_page,
                }
            )
        return rows

    # ----------------------------------------------------------- listeners
    def _on_console(self, page, msg):
        try:
            self._console[page].append(f"[{msg.type}] {msg.text}")
        except Exception:
            pass

    def _on_dialog(self, dialog):
        self._last_dialog = {"type": dialog.type, "message": dialog.message}
        self._schedule(self._handle_dialog(dialog))

    async def _handle_dialog(self, dialog):
        try:
            if self._dialog_accept:
                if self._dialog_prompt is not None and dialog.type == "prompt":
                    await dialog.accept(self._dialog_prompt)
                else:
                    await dialog.accept()
            else:
                await dialog.dismiss()
        except Exception:
            pass

    def _on_download(self, download):
        if browser_knob(self.config, "downloads", True):
            self._schedule(self._save_download(download))

    async def _save_download(self, download):
        try:
            dirp = Path(self.config.state_dir) / "downloads"
            dirp.mkdir(parents=True, exist_ok=True)
            name = download.suggested_filename or "download.bin"
            path = dirp / name
            await download.save_as(str(path))
            self._downloads.append({"filename": name, "path": str(path), "url": download.url})
        except Exception as e:  # noqa: BLE001
            log.warning("download capture failed: %s", e)

    def _schedule(self, coro):
        try:
            asyncio.ensure_future(coro)
        except Exception:
            pass

    # ----------------------------------------------------------- snapshot / refs
    async def snapshot(
        self,
        *,
        targetId=None,
        interactive: bool = False,
        compact: bool = False,
        depth: int | None = None,
        max_chars: int = DEFAULT_AI_SNAPSHOT_MAX_CHARS,
        urls: bool = False,
        labels: bool = False,
        selector: str | None = None,
        frame: str | None = None,
    ) -> str:
        await self.ensure()
        page = self.resolve_target(targetId)
        self.active_page = page  # follow-up acts without a targetId stay on this tab
        if frame:
            loc = page.frame_locator(frame).locator(selector or "body")
        else:
            loc = page.locator(selector or "body")
        raw = await loc.aria_snapshot(mode="ai", boxes=labels)
        text = format_ai_snapshot(raw, depth=depth, compact=compact, urls=urls, max_chars=max_chars)
        title = await page.title()
        extra = await self._cursor_scan(page, labels=labels)
        note = await self._auth_note(page)
        return (
            f"Page: {title}\nURL: {page.url}\nTab: {self.tab_handle(page)}\n\n{text}{extra}{note}"
        )

    async def _cursor_scan(self, page, labels: bool = False) -> str:
        """List non-ARIA clickable elements with [ref=cN] (see _CURSOR_SCAN_JS)."""
        if not browser_knob(self.config, "cursor_scan", True):
            return ""
        try:
            items = await page.evaluate(_CURSOR_SCAN_JS)
        except Exception:  # noqa: BLE001
            return ""
        if not items:
            return ""
        lines = []
        for it in items:
            name = f' "{it["text"]}"' if it.get("text") else ""
            box = f" [box={','.join(str(b) for b in it['box'])}]" if labels else ""
            lines.append(f"- clickable{name} <{it['tag']}> [ref={it['ref']}]{box}")
        return (
            "\n\n[non-ARIA clickables — interactive elements the accessibility tree misses; "
            "use these refs like any other]\n" + "\n".join(lines)
        )

    async def _auth_note(self, page) -> str:
        """Generic sign-in signal (no site-specific rules): if the page has a
        password field it's almost certainly a login wall — surface that so the
        agent pauses and asks the user instead of flailing or scraping elsewhere."""
        try:
            if await page.locator("input[type=password]").count():
                return (
                    "\n\n[auth] This page has a sign-in form (password field). If it gates "
                    "what the task needs, STOP here: ask the user to log in in this window and "
                    "reply when done — do NOT retry, search again, or open Google/Bing."
                )
        except Exception:  # noqa: BLE001
            pass
        return ""

    def resolve_ref(self, ref: str, page=None):
        # Two ref schemes: cursor-scan refs (cN) resolve by our data attribute;
        # everything else is a Playwright aria-ref (e3 / f1e2 — frame already encoded,
        # so resolution is page-level, no frame_locator wrapping).
        page = page or self.active_page
        if ref[:1] == "c" and ref[1:].isdigit():
            return page.locator(f'[data-agentd-ci="{ref}"]')
        return page.locator(f"aria-ref={ref}")

    # ----------------------------------------------------------- introspection
    async def status(self) -> dict:
        running = self.context is not None
        tabs = self.list_tabs() if running else []
        return {
            "running": running,
            "mode": self.mode,
            "headless": getattr(self.config, "browser_headless", True),
            "tabCount": len(tabs),
            "tabs": tabs,
            "downloads": list(self._downloads),
        }

    async def doctor(self) -> dict:
        report = {"mode": self.mode, "playwright": True, "ok": True, "issues": []}
        try:
            from playwright._impl._driver import compute_driver_executable  # noqa: F401
        except Exception as e:  # noqa: BLE001
            report["issues"].append(f"driver import: {e}")
        try:
            await self.ensure()
            report["contextAlive"] = self.context is not None
            report["tabCount"] = len(self.context.pages)
        except Exception as e:  # noqa: BLE001
            report["ok"] = False
            report["issues"].append(f"ensure: {type(e).__name__}: {e}")
        report["ok"] = report["ok"] and not report["issues"]
        return report

    async def profiles(self) -> dict:
        return {"active": self.mode, "available": [self.mode]}

    # ----------------------------------------------------------- extras
    def console_logs(self, page, limit: int | None = None) -> list[str]:
        logs = list(self._console.get(page, []))
        return logs[-limit:] if limit else logs

    async def pdf(self, page, path: str) -> None:
        await page.pdf(path=path)
