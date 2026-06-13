"""browser tool: Playwright-driven Chrome with rich ARIA-snapshot perception.

Faithful port of OpenClaw's browser tool core (extensions/browser/src/browser/):
  actions: navigate | snapshot | act | screenshot | tabs
  act kinds: click | clickCoords | type | fill | press | select | hover |
             scrollIntoView | drag | wait | evaluate | resize | batch

Key perception features ported so long / lazy-loaded pages work:
  - snapshot planning: interactive / compact / depth / limit / max_chars /
    mode="efficient" (efficient => interactive, compact, depth=6, 8k chars)
  - scrollIntoView + a real wait (networkidle/text/selector/url/fn) so the
    model can reveal and gather lazy-loaded list items
  - role sets ported verbatim from snapshot-roles.ts
  - stale-ref recovery guidance
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from . import Tool, ToolResult

# constants.ts
DEFAULT_AI_SNAPSHOT_MAX_CHARS = 40_000
DEFAULT_AI_SNAPSHOT_EFFICIENT_MAX_CHARS = 8_000
DEFAULT_AI_SNAPSHOT_EFFICIENT_DEPTH = 6

# snapshot-roles.ts (verbatim)
INTERACTIVE_ROLES = {
    "button", "checkbox", "combobox", "link", "listbox", "menuitem",
    "menuitemcheckbox", "menuitemradio", "option", "radio", "searchbox",
    "slider", "spinbutton", "switch", "tab", "textbox", "treeitem",
}
CONTENT_ROLES = {
    "article", "cell", "columnheader", "gridcell", "heading", "listitem",
    "main", "navigation", "region", "rowheader",
}
STRUCTURAL_ROLES = {
    "application", "directory", "document", "generic", "grid", "group",
    "ignored", "list", "menu", "menubar", "none", "presentation", "row",
    "rowgroup", "table", "tablist", "toolbar", "tree", "treegrid",
}

# aria_snapshot lines: `  - button "Submit"` / `- link "Home":` / `- heading "Hi" [level=1]`
_NODE_RE = re.compile(r"^(\s*)-\s+([a-z]+)(?:\s+\"((?:[^\"\\]|\\.)*)\")?(.*)$")
_NETWORKIDLE_TIMEOUT_MS = 8_000


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

    async def settle(self):
        """Best-effort wait for the network to go idle after nav/interaction."""
        try:
            await self.active_page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS)
        except Exception:
            pass

    # ----------------------------------------------------------- snapshot

    async def snapshot(
        self,
        *,
        interactive: bool = False,
        compact: bool = False,
        depth: int | None = None,
        max_chars: int = DEFAULT_AI_SNAPSHOT_MAX_CHARS,
    ) -> str:
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
            indent_depth = len(indent) // 2
            if depth is not None and indent_depth > depth:
                continue
            is_interactive = role in INTERACTIVE_ROLES
            if interactive and not is_interactive and role not in CONTENT_ROLES:
                continue
            if compact and role in STRUCTURAL_ROLES and not name:
                continue
            if is_interactive:
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
            text = text[:max_chars] + "\n\n[...TRUNCATED - page too large; use mode=efficient or a higher limit]"
        title = await page.title()
        return f"Page: {title}\nURL: {page.url}\n\n{text}"

    def resolve_ref(self, ref: str):
        entry = self.ref_map.get(ref)
        if entry is None:
            raise ValueError(
                f"Unknown ref '{ref}'. Run a new snapshot and use a ref from that snapshot."
            )
        page = self.active_page
        loc = (
            page.get_by_role(entry["role"], name=entry["name"], exact=True)
            if entry["name"]
            else page.get_by_role(entry["role"])
        )
        return loc.nth(entry["nth"])


def _resolve_snapshot_plan(params: dict) -> dict:
    mode = params.get("mode")
    if mode == "efficient":
        return {
            "interactive": params.get("interactive", True),
            "compact": params.get("compact", True),
            "depth": params.get("depth", DEFAULT_AI_SNAPSHOT_EFFICIENT_DEPTH),
            "max_chars": params.get("max_chars", DEFAULT_AI_SNAPSHOT_EFFICIENT_MAX_CHARS),
        }
    return {
        "interactive": params.get("interactive", False),
        "compact": params.get("compact", False),
        "depth": params.get("depth"),
        "max_chars": params.get("max_chars", DEFAULT_AI_SNAPSHOT_MAX_CHARS),
    }


class BrowserTool(Tool):
    name = "browser"
    description = (
        "Control a web browser. Actions: navigate, snapshot, act, screenshot, tabs.\n"
        "snapshot returns the page as an accessibility tree with [ref=eN] markers; params: "
        "mode=\"efficient\" (compact, interactive-only, faster on big pages), interactive, "
        "compact, depth, max_chars.\n"
        "act kinds: click, clickCoords, type, fill, press, select, hover, scrollIntoView, "
        "drag, wait, evaluate, resize, batch. Use a ref from the latest snapshot.\n"
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
                         "hover", "scrollIntoView", "drag", "wait", "evaluate", "resize", "batch"],
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
            # batch
            "actions": {"type": "array", "items": {"type": "object"}, "description": "batch: sub-actions."},
            "stop_on_error": {"type": "boolean"},
            # tabs
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

    async def _snapshot_text(self, params: dict) -> str:
        return await self.manager.snapshot(**_resolve_snapshot_plan(params))

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
        if kind == "batch":
            return await self._batch(params)
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

    async def _batch(self, params: dict) -> ToolResult:
        actions = params.get("actions") or []
        stop_on_error = params.get("stop_on_error", True)
        results = []
        for i, sub in enumerate(actions):
            sub = {**sub, "action": "act"}
            try:
                r = await self._act(sub)
                results.append(f"[{i}] {'ERR' if r.is_error else 'ok'}")
                if r.is_error and stop_on_error:
                    results.append(f"  stopped: {r.content[0].text[:120] if r.content else ''}")
                    break
            except Exception as e:
                results.append(f"[{i}] ERR {type(e).__name__}: {e}")
                if stop_on_error:
                    break
        snap = await self._snapshot_text(params)
        return ToolResult.text("batch: " + " ".join(results) + "\n\n" + snap)

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
