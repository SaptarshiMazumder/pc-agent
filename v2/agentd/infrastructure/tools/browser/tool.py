"""browser tool: Playwright-driven Chrome with rich ARIA-snapshot perception.

The dispatcher over a BrowserProvider (Playwright today). Faithful port of
OpenClaw's browser tool core (extensions/browser/src/browser/):
  actions: navigate | snapshot | act | screenshot | tabs
  act kinds: click | clickCoords | type | fill | press | select | hover |
             scrollIntoView | drag | wait | evaluate | resize | batch
"""

from __future__ import annotations

import time
from pathlib import Path

from agentd.infrastructure.tools.browser.snapshot import resolve_snapshot_plan

from .. import Tool, ToolResult


class BrowserTool(Tool):
    name = "browser"
    default_timeout_sec = 180.0  # a single browser action (navigate/snapshot/act)
    default_retryable = False
    description = (
        "Control a web browser. Actions: navigate, snapshot, act, screenshot, tabs.\n"
        "snapshot returns the page as an accessibility tree with [ref=eN] markers; params: "
        "mode=\"efficient\" (compact, interactive-only, faster on big pages), interactive, "
        "compact, depth, max_chars.\n"
        "act kinds: click, clickCoords, type, fill, press, select, hover, scrollIntoView, "
        "drag, wait, evaluate, resize. Use a ref from the latest snapshot. Call act once per "
        "action (e.g. click a box, then a second act to type into it).\n"
        "wait supports load_state (load|domcontentloaded|networkidle), text, text_gone, "
        "selector, url, fn, time_ms — use it (esp. networkidle) after navigation/scroll.\n"
        "For long/lazy lists: scrollIntoView the last item (or evaluate window.scrollBy), "
        "wait networkidle, then snapshot again; repeat to gather more."
    )
    label = "Browser"
    concurrency = "sequential"
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["navigate", "snapshot", "act", "screenshot", "tabs"]},
            "url": {"type": "string", "description": "URL (for navigate / tabs open)."},
            # snapshot planning
            "mode": {"type": "string", "enum": ["efficient"], "description": "efficient snapshot preset."},
            "interactive": {"type": "boolean", "description": "Snapshot: interactive elements only."},
            "compact": {"type": "boolean", "description": "Snapshot: drop unnamed structural nodes."},
            "depth": {"type": "integer", "minimum": 1, "description": "Snapshot: max tree depth."},
            "max_chars": {"type": "integer", "minimum": 500, "description": "Snapshot: char cap."},
            # act
            "kind": {
                "type": "string",
                "enum": ["click", "clickCoords", "type", "fill", "press", "select",
                         "hover", "scrollIntoView", "drag", "wait", "evaluate", "resize"],
                "description": "Sub-action for act.",
            },
            "ref": {"type": "string", "description": "Element ref from the last snapshot (e.g. 'e3')."},
            "text": {"type": "string", "description": "Text for type/fill."},
            "key": {"type": "string", "description": "Key for press (e.g. 'Enter')."},
            "value": {"type": "string", "description": "Single option value for select."},
            "values": {"type": "array", "items": {"type": "string"}, "description": "Option values for select."},
            "expression": {"type": "string", "description": "JavaScript for evaluate."},
            "x": {"type": "number", "description": "X for clickCoords."},
            "y": {"type": "number", "description": "Y for clickCoords."},
            "start_ref": {"type": "string", "description": "Drag source ref."},
            "end_ref": {"type": "string", "description": "Drag target ref."},
            "width": {"type": "integer", "description": "Viewport width for resize."},
            "height": {"type": "integer", "description": "Viewport height for resize."},
            "submit": {"type": "boolean", "description": "type: press Enter after."},
            # wait
            "time_ms": {"type": "integer", "minimum": 0},
            "load_state": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"]},
            "text_gone": {"type": "string"},
            "selector": {"type": "string"},
            "fn": {"type": "string", "description": "JS predicate for wait."},
            "timeout_ms": {"type": "integer", "minimum": 0},
            # tabs
            "tab_action": {"type": "string", "enum": ["list", "open", "close", "focus"]},
            "tab_index": {"type": "integer", "minimum": 0},
        },
    }

    def __init__(self, config, manager):
        self.config = config
        self.manager = manager  # BrowserProvider (PlaywrightBrowserProvider today)

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            return await self._execute(params)
        except Exception as e:
            return ToolResult.text(f"browser error: {type(e).__name__}: {e}", is_error=True)

    async def _snapshot_text(self, params: dict) -> str:
        return await self.manager.snapshot(**resolve_snapshot_plan(params))

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
            await mgr.settle()
            return ToolResult.text(await self._snapshot_text(params))

        if action == "snapshot":
            return ToolResult.text(await self._snapshot_text(params))

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
        timeout = params.get("timeout_ms")

        # Non-ref kinds first.
        if kind == "wait":
            await self._wait(params)
            return ToolResult.text(await self._snapshot_text(params))
        if kind == "evaluate":
            expr = params.get("expression")
            if not expr:
                return ToolResult.text("evaluate requires expression", is_error=True)
            if params.get("ref"):
                result = await mgr.resolve_ref(params["ref"]).evaluate(expr)
            else:
                result = await page.evaluate(expr)
            return ToolResult.text(f"evaluate result: {result!r}")
        if kind == "clickCoords":
            await page.mouse.click(params.get("x", 0), params.get("y", 0))
            await mgr.settle()
            return ToolResult.text(await self._snapshot_text(params))
        if kind == "resize":
            await page.set_viewport_size({"width": params.get("width", 1280), "height": params.get("height", 800)})
            return ToolResult.text(await self._snapshot_text(params))
        if kind == "drag":
            src = mgr.resolve_ref(params["start_ref"])
            dst = mgr.resolve_ref(params["end_ref"])
            await src.drag_to(dst)
            await mgr.settle()
            return ToolResult.text(await self._snapshot_text(params))
        if kind == "press" and not params.get("ref"):
            await page.keyboard.press(params.get("key") or "Enter")
            await mgr.settle()
            return ToolResult.text(await self._snapshot_text(params))

        ref = params.get("ref")
        if not ref:
            return ToolResult.text(f"act kind={kind} requires ref", is_error=True)
        loc = mgr.resolve_ref(ref)

        if kind == "click":
            await loc.click(timeout=timeout)
            await mgr.settle()
        elif kind == "fill":
            await loc.fill(params.get("text") or "", timeout=timeout)
        elif kind == "type":
            await loc.press_sequentially(params.get("text") or "")
            if params.get("submit"):
                await loc.press("Enter")
                await mgr.settle()
        elif kind == "press":
            await loc.press(params.get("key") or "Enter")
            await mgr.settle()
        elif kind == "select":
            values = params.get("values") or ([params["value"]] if params.get("value") else [])
            await loc.select_option(values, timeout=timeout)
        elif kind == "hover":
            await loc.hover(timeout=timeout)
        elif kind == "scrollIntoView":
            await loc.scroll_into_view_if_needed(timeout=timeout)
            await mgr.settle()
        else:
            return ToolResult.text(f"Unknown act kind: {kind}", is_error=True)

        return ToolResult.text(await self._snapshot_text(params))

    async def _wait(self, params: dict) -> None:
        page = self.manager.active_page
        timeout = params.get("timeout_ms") or 15_000
        if params.get("time_ms") is not None:
            await page.wait_for_timeout(params["time_ms"])
        if params.get("load_state"):
            await page.wait_for_load_state(params["load_state"], timeout=timeout)
        if params.get("text"):
            await page.get_by_text(params["text"]).first.wait_for(state="visible", timeout=timeout)
        if params.get("text_gone"):
            await page.get_by_text(params["text_gone"]).first.wait_for(state="hidden", timeout=timeout)
        if params.get("selector"):
            await page.locator(params["selector"]).first.wait_for(state="visible", timeout=timeout)
        if params.get("url"):
            await page.wait_for_url(params["url"], timeout=timeout)
        if params.get("fn"):
            await page.wait_for_function(params["fn"], timeout=timeout)

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
                await mgr.settle()
                return ToolResult.text(await self._snapshot_text(params))
            return ToolResult.text("Opened new blank tab.")

        index = params.get("tab_index")
        if index is None or index >= len(pages):
            return ToolResult.text(f"Invalid tab_index: {index}", is_error=True)

        if tab_action == "focus":
            mgr.active_page = pages[index]
            await mgr.active_page.bring_to_front()
            return ToolResult.text(await self._snapshot_text(params))

        if tab_action == "close":
            closing_active = pages[index] is mgr.active_page
            await pages[index].close()
            if closing_active:
                remaining = mgr.context.pages
                mgr.active_page = remaining[-1] if remaining else await mgr.context.new_page()
            return ToolResult.text(f"Closed tab {index}.")

        return ToolResult.text(f"Unknown tab_action: {tab_action}", is_error=True)
