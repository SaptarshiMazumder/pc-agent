"""browser tool: Playwright-driven Chrome control with ARIA snapshots + refs.

Mirrors the reference browser tool's core:
  actions: navigate | snapshot | act | screenshot | tabs
  act kinds: click | fill | type | press | select | hover | wait | evaluate

Snapshot = Playwright aria_snapshot() post-processed so each interactive node
gets a stable [ref=eN] marker; act() resolves refs back to locators via
get_by_role(role, name).nth(i). Every state-changing action returns a fresh
snapshot so the model always sees the resulting page state.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from . import Tool, ToolResult

SNAPSHOT_MAX_CHARS = 30_000
INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox",
    "listbox", "option", "menuitem", "menuitemcheckbox", "menuitemradio",
    "slider", "spinbutton", "switch", "tab", "textarea",
}

# aria_snapshot lines look like:  - button "Submit"  /  - link "Home":  /  - heading "Hi" [level=1]
_NODE_RE = re.compile(r"^(\s*)-\s+([a-z]+)(?:\s+\"((?:[^\"\\]|\\.)*)\")?(.*)$")


class BrowserManager:
    """Lazy singleton owning the Playwright browser; lives on the gateway loop."""

    def __init__(self, config):
        import playwright  # noqa: F401  (raise ImportError early if missing)

        self.config = config
        self._pw = None
        self._browser = None
        self.context = None
        self.active_page = None
        self.ref_map: dict[str, dict] = {}

    async def ensure(self):
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.config.browser_headless)
        self.context = await self._browser.new_context()
        self.active_page = await self.context.new_page()

    async def close(self):
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._browser = None
        self._pw = None

    # ----------------------------------------------------------- snapshot

    async def snapshot(self, max_chars: int = SNAPSHOT_MAX_CHARS) -> str:
        await self.ensure()
        page = self.active_page
        raw = await page.locator("body").aria_snapshot()
        self.ref_map = {}
        counter = 0
        seen: dict[tuple[str, str], int] = {}
        out_lines = []
        for line in raw.splitlines():
            m = _NODE_RE.match(line)
            if not m:
                out_lines.append(line)
                continue
            indent, role, name, rest = m.group(1), m.group(2), m.group(3), m.group(4)
            if role in INTERACTIVE_ROLES:
                counter += 1
                ref = f"e{counter}"
                key = (role, name or "")
                nth = seen.get(key, 0)
                seen[key] = nth + 1
                self.ref_map[ref] = {"role": role, "name": name or "", "nth": nth}
                name_part = f' "{name}"' if name else ""
                out_lines.append(f"{indent}- {role}{name_part}{rest} [ref={ref}]")
            else:
                out_lines.append(line)
        text = "\n".join(out_lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… [snapshot truncated]"
        title = await page.title()
        return f"Page: {title}\nURL: {page.url}\n\n{text}"

    def resolve_ref(self, ref: str):
        entry = self.ref_map.get(ref)
        if entry is None:
            raise ValueError(
                f"Unknown ref '{ref}'. Take a fresh snapshot and use a ref from it."
            )
        page = self.active_page
        loc = (
            page.get_by_role(entry["role"], name=entry["name"], exact=True)
            if entry["name"]
            else page.get_by_role(entry["role"])
        )
        return loc.nth(entry["nth"])


class BrowserTool(Tool):
    name = "browser"
    description = (
        "Control a web browser. Actions: navigate, snapshot, act, screenshot, tabs. "
        "Snapshots return the page as an accessibility tree with [ref=eN] markers; "
        "use act with a ref to click/fill/type on elements."
    )
    label = "Browser"
    concurrency = "sequential"
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "snapshot", "act", "screenshot", "tabs"],
            },
            "url": {"type": "string", "description": "URL (for navigate / tabs open)."},
            "kind": {
                "type": "string",
                "enum": ["click", "fill", "type", "press", "select", "hover", "wait", "evaluate"],
                "description": "Sub-action for act.",
            },
            "ref": {"type": "string", "description": "Element ref from the last snapshot (e.g. 'e3')."},
            "text": {"type": "string", "description": "Text for fill/type."},
            "key": {"type": "string", "description": "Key for press (e.g. 'Enter')."},
            "value": {"type": "string", "description": "Option value for select."},
            "expression": {"type": "string", "description": "JavaScript for evaluate."},
            "time_ms": {"type": "integer", "minimum": 0, "description": "Wait duration for kind=wait."},
            "tab_action": {"type": "string", "enum": ["list", "open", "close", "focus"]},
            "tab_index": {"type": "integer", "minimum": 0},
        },
    }

    def __init__(self, config, manager: BrowserManager):
        self.config = config
        self.manager = manager

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            return await self._execute(params)
        except Exception as e:
            return ToolResult.text(f"browser error: {type(e).__name__}: {e}", is_error=True)

    async def _execute(self, params) -> ToolResult:
        action = params["action"]
        mgr = self.manager
        await mgr.ensure()
        page = mgr.active_page

        if action == "navigate":
            url = params.get("url")
            if not url:
                return ToolResult.text("navigate requires url", is_error=True)
            await page.goto(url, wait_until="domcontentloaded")
            return ToolResult.text(await mgr.snapshot())

        if action == "snapshot":
            return ToolResult.text(await mgr.snapshot())

        if action == "act":
            return await self._act(params)

        if action == "screenshot":
            shots_dir = Path(self.config.state_dir) / "screenshots"
            shots_dir.mkdir(parents=True, exist_ok=True)
            path = shots_dir / f"shot-{int(time.time() * 1000)}.png"
            await page.screenshot(path=str(path), full_page=False)
            return ToolResult.text(f"Screenshot saved: {path}")

        if action == "tabs":
            return await self._tabs(params)

        return ToolResult.text(f"Unknown action: {action}", is_error=True)

    async def _act(self, params) -> ToolResult:
        mgr = self.manager
        page = mgr.active_page
        kind = params.get("kind")
        if not kind:
            return ToolResult.text("act requires kind", is_error=True)

        if kind == "wait":
            await asyncio.sleep((params.get("time_ms") or 1000) / 1000)
            return ToolResult.text(await mgr.snapshot())

        if kind == "evaluate":
            expression = params.get("expression")
            if not expression:
                return ToolResult.text("evaluate requires expression", is_error=True)
            result = await page.evaluate(expression)
            return ToolResult.text(f"evaluate result: {result!r}")

        if kind == "press" and not params.get("ref"):
            await page.keyboard.press(params.get("key") or "Enter")
            await page.wait_for_load_state("domcontentloaded")
            return ToolResult.text(await mgr.snapshot())

        ref = params.get("ref")
        if not ref:
            return ToolResult.text(f"act kind={kind} requires ref", is_error=True)
        loc = mgr.resolve_ref(ref)

        if kind == "click":
            await loc.click()
            await page.wait_for_load_state("domcontentloaded")
        elif kind == "fill":
            await loc.fill(params.get("text") or "")
        elif kind == "type":
            await loc.press_sequentially(params.get("text") or "")
        elif kind == "press":
            await loc.press(params.get("key") or "Enter")
            await page.wait_for_load_state("domcontentloaded")
        elif kind == "select":
            await loc.select_option(params.get("value") or "")
        elif kind == "hover":
            await loc.hover()
        else:
            return ToolResult.text(f"Unknown act kind: {kind}", is_error=True)

        return ToolResult.text(await mgr.snapshot())

    async def _tabs(self, params) -> ToolResult:
        mgr = self.manager
        tab_action = params.get("tab_action") or "list"
        pages = mgr.context.pages

        if tab_action == "list":
            lines = []
            for i, p in enumerate(pages):
                marker = "*" if p is mgr.active_page else " "
                lines.append(f"{marker} [{i}] {await p.title()}  {p.url}")
            return ToolResult.text("Tabs:\n" + "\n".join(lines))

        if tab_action == "open":
            new_page = await mgr.context.new_page()
            mgr.active_page = new_page
            if params.get("url"):
                await new_page.goto(params["url"], wait_until="domcontentloaded")
                return ToolResult.text(await mgr.snapshot())
            return ToolResult.text("Opened new blank tab.")

        index = params.get("tab_index")
        if index is None or index >= len(pages):
            return ToolResult.text(f"Invalid tab_index: {index}", is_error=True)

        if tab_action == "focus":
            mgr.active_page = pages[index]
            await mgr.active_page.bring_to_front()
            return ToolResult.text(await mgr.snapshot())

        if tab_action == "close":
            closing_active = pages[index] is mgr.active_page
            await pages[index].close()
            if closing_active:
                remaining = mgr.context.pages
                mgr.active_page = remaining[-1] if remaining else await mgr.context.new_page()
            return ToolResult.text(f"Closed tab {index}.")

        return ToolResult.text(f"Unknown tab_action: {tab_action}", is_error=True)
