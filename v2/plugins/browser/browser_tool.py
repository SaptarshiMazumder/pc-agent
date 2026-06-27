"""browser tool: Playwright/CDP-driven Chrome with rich ARIA-snapshot perception.

The dispatcher over a BrowserProvider. Faithful port of OpenClaw's browser tool,
at capability parity (and beyond on durable aria refs):
  actions: navigate | snapshot | act | screenshot | tabs | open | focus | close |
           console | pdf | dialog | upload | status | doctor | profiles
  act kinds: click | clickCoords | type | fill | press | select | hover |
             scrollIntoView | drag | wait | evaluate | resize

Perception: snapshot returns Playwright's native AI aria-tree with durable
[ref=eN] markers (resolved via the aria-ref selector — stable across calls until
the page changes). Use a ref from the LATEST snapshot of the SAME tab.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agentd.infrastructure.tools.browser.snapshot import resolve_snapshot_plan

from agentd.application.interfaces.tool import Tool, ToolResult

_MODIFIER_ALIASES = {
    "ctrl": "Control", "control": "Control", "cmd": "Meta", "command": "Meta",
    "meta": "Meta", "shift": "Shift", "alt": "Alt", "option": "Alt",
}

# Why an action failed: stale / off-screen / covered (agent-browser-style diagnostic).
_COVER_CHECK_JS = r"""el => {
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return {state: 'hidden'};
  const cx = r.left + r.width/2, cy = r.top + r.height/2;
  if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) return {state: 'offscreen'};
  const top = document.elementFromPoint(cx, cy);
  if (!top) return {state: 'offscreen'};
  if (top === el || el.contains(top) || top.contains(el)) return {state: 'ok'};
  const id = top.id ? ('#' + top.id) : '';
  const cls = (typeof top.className === 'string' && top.className.trim())
      ? ('.' + top.className.trim().split(/\s+/)[0]) : '';
  return {state: 'covered', by: top.tagName.toLowerCase() + id + cls};
}"""


class BrowserTool(Tool):
    name = "browser"
    default_timeout_sec = 180.0
    default_retryable = False
    description = (
        "Control a web browser using YOUR signed-in session. Use this whenever a task needs "
        "a logged-in account, interaction (clicking/typing/forms), or content the public "
        "web_search/web_fetch tools can't reach (login-walled, crawler-blocked, or JS-only "
        "pages) — e.g. reading your own messages, private dashboards, or social networks.\n"
        "Control a web browser. Actions: navigate, snapshot, act, screenshot, tabs, open, "
        "focus, close, console, pdf, dialog, upload, status, doctor, profiles.\n"
        "snapshot returns the page as an accessibility tree with durable [ref=eN] markers; "
        "params: mode=\"efficient\" (compact, faster on big pages), depth, max_chars, urls "
        "(include link targets), labels (add [box] coords for clickCoords), selector/frame "
        "(scope to a subtree/iframe), target_id (which tab).\n"
        "act kinds: click (+double_click, button, modifiers), clickCoords, type (+slowly, "
        "submit), fill (single ref or fields=[{ref,text},...] for whole forms), press, select, "
        "hover, scrollIntoView, drag, wait, evaluate, resize. Pass a ref from the latest "
        "snapshot of the same tab; pass target_id to act on a specific tab; frame to act inside "
        "an iframe.\n"
        "Tabs use STABLE handles: open with a label, reuse via target_id (label or tabId like "
        "t2) — never positional indices.\n"
        "wait supports load_state (load|domcontentloaded|networkidle), text, text_gone, selector, "
        "url, fn, time_ms — use networkidle after navigation/scroll. For long/lazy lists: "
        "scrollIntoView the last item, wait networkidle, then snapshot again; repeat."
    )
    label = "Browser"
    concurrency = "sequential"
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": [
                "navigate", "snapshot", "act", "screenshot", "tabs", "open", "focus", "close",
                "console", "pdf", "dialog", "upload", "status", "doctor", "profiles",
            ]},
            "url": {"type": "string", "description": "URL (navigate / open)."},
            "target_id": {"type": "string", "description": "Tab handle: a label or tabId (e.g. 't2'). Omit = active tab."},
            "label": {"type": "string", "description": "Stable label to give a tab on open."},
            # snapshot planning
            "mode": {"type": "string", "enum": ["efficient"], "description": "efficient snapshot preset."},
            "interactive": {"type": "boolean", "description": "Snapshot: focus interactive elements."},
            "compact": {"type": "boolean", "description": "Snapshot: drop unnamed structural nodes."},
            "depth": {"type": "integer", "minimum": 1, "description": "Snapshot: max tree depth."},
            "max_chars": {"type": "integer", "minimum": 500, "description": "Snapshot: char cap."},
            "refs": {"type": "string", "enum": ["aria", "role"], "description": "Ref style (aria = durable, default)."},
            "urls": {"type": "boolean", "description": "Snapshot: include link /url targets."},
            "labels": {"type": "boolean", "description": "Snapshot/screenshot: include [box] coordinates."},
            "selector": {"type": "string", "description": "Scope snapshot / wait to a CSS selector subtree."},
            "frame": {"type": "string", "description": "CSS selector of an iframe to operate inside."},
            # act
            "kind": {"type": "string", "enum": [
                "click", "clickCoords", "type", "fill", "press", "select",
                "hover", "scrollIntoView", "drag", "wait", "evaluate", "resize"],
                "description": "Sub-action for act."},
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
            "double_click": {"type": "boolean", "description": "click: double-click."},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "click: mouse button."},
            "modifiers": {"type": "array", "items": {"type": "string"}, "description": "click: held keys (Control/Shift/Alt/Meta)."},
            "slowly": {"type": "boolean", "description": "type: per-character typing (React/contenteditable)."},
            "delay_ms": {"type": "integer", "minimum": 0, "description": "type/press: inter-key delay."},
            "fields": {"type": "array", "items": {"type": "object"}, "description": "fill: [{ref|selector, text|value}, ...] — fill many in one act."},
            # wait
            "time_ms": {"type": "integer", "minimum": 0},
            "load_state": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"]},
            "text_gone": {"type": "string"},
            "fn": {"type": "string", "description": "JS predicate for wait."},
            "timeout_ms": {"type": "integer", "minimum": 0},
            # screenshot
            "full_page": {"type": "boolean", "description": "screenshot: capture the full scrollable page."},
            "image_type": {"type": "string", "enum": ["png", "jpeg"], "description": "screenshot format (default png)."},
            "element": {"type": "string", "description": "screenshot: CSS selector of a single element to capture."},
            # console
            "limit": {"type": "integer", "minimum": 1, "description": "console: max recent messages."},
            # dialog
            "accept": {"type": "boolean", "description": "dialog: accept (true) or dismiss (false) the next dialog(s)."},
            "prompt_text": {"type": "string", "description": "dialog: text to enter for a prompt() dialog."},
            # upload
            "paths": {"type": "array", "items": {"type": "string"}, "description": "upload: local file paths."},
            "input_ref": {"type": "string", "description": "upload: ref/selector of the file <input>."},
            # tabs (back-compat)
            "tab_action": {"type": "string", "enum": ["list", "open", "close", "focus"]},
            "tab_index": {"type": "integer", "minimum": 0},
        },
    }

    def __init__(self, config, manager):
        self.config = config
        self.manager = manager

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            return await self._execute(params)
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"browser error: {type(e).__name__}: {e}", is_error=True)

    def _target(self, params):
        return params.get("target_id") or params.get("targetId")

    async def _snapshot_text(self, params: dict, target_id=None) -> str:
        plan = resolve_snapshot_plan(params)
        return await self.manager.snapshot(targetId=target_id or self._target(params), **plan)

    async def _execute(self, params) -> ToolResult:
        action = params["action"]
        mgr = self.manager
        await mgr.ensure()

        if action == "status":
            return self._json(await mgr.status())
        if action == "doctor":
            return self._json(await mgr.doctor())
        if action == "profiles":
            return self._json(await mgr.profiles())

        if action == "navigate":
            url = params.get("url")
            if not url:
                return ToolResult.text("navigate requires url", is_error=True)
            page = mgr.resolve_target(self._target(params))
            mgr.active_page = page
            await page.goto(url, wait_until="domcontentloaded")
            await mgr.settle()
            return ToolResult.text(await self._snapshot_text(params))

        if action == "snapshot":
            return ToolResult.text(await self._snapshot_text(params))

        if action == "act":
            return await self._act(params)

        if action == "screenshot":
            return await self._screenshot(params)

        if action in ("tabs", "open", "focus", "close"):
            return await self._tabs(action, params)

        if action == "console":
            page = mgr.resolve_target(self._target(params))
            logs = mgr.console_logs(page, params.get("limit"))
            return ToolResult.text("Console (%d):\n%s" % (len(logs), "\n".join(logs) or "(empty)"))

        if action == "pdf":
            page = mgr.resolve_target(self._target(params))
            out = Path(self.config.state_dir) / "pdf"
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"page-{int(time.time() * 1000)}.pdf"
            try:
                await mgr.pdf(page, str(path))
            except Exception as e:  # noqa: BLE001
                return ToolResult.text(
                    f"pdf failed ({type(e).__name__}: {e}). PDF export needs headless Chromium.",
                    is_error=True,
                )
            return ToolResult.text(f"PDF saved: {path}")

        if action == "dialog":
            if "accept" in params:
                mgr._dialog_accept = bool(params["accept"])
            if "prompt_text" in params:
                mgr._dialog_prompt = params["prompt_text"]
            last = getattr(mgr, "_last_dialog", None)
            return self._json({
                "dialogAccept": mgr._dialog_accept,
                "promptText": mgr._dialog_prompt,
                "lastDialog": last,
            })

        if action == "upload":
            return await self._upload(params)

        return ToolResult.text(f"Unknown action: {action}", is_error=True)

    async def _act(self, params) -> ToolResult:
        mgr = self.manager
        target_id = self._target(params)
        page = mgr.resolve_target(target_id)
        mgr.active_page = page
        frame = params.get("frame")
        kind = params.get("kind")
        if not kind:
            return ToolResult.text("act requires kind", is_error=True)
        timeout = params.get("timeout_ms")

        # ---- non-ref kinds -------------------------------------------------
        if kind == "wait":
            await self._wait(page, params)
            return ToolResult.text(await self._snapshot_text(params, target_id))
        if kind == "evaluate":
            expr = params.get("expression")
            if not expr:
                return ToolResult.text("evaluate requires expression", is_error=True)
            if params.get("ref"):
                result = await mgr.resolve_ref(params["ref"], page).evaluate(expr)
            else:
                result = await page.evaluate(expr)
            return ToolResult.text(f"evaluate result: {result!r}")
        if kind == "clickCoords":
            await page.mouse.click(params.get("x", 0), params.get("y", 0))
            await mgr.settle()
            return ToolResult.text(await self._snapshot_text(params, target_id))
        if kind == "resize":
            await page.set_viewport_size(
                {"width": params.get("width", 1280), "height": params.get("height", 800)}
            )
            return ToolResult.text(await self._snapshot_text(params, target_id))
        if kind == "drag":
            src = mgr.resolve_ref(params["start_ref"], page)
            dst = mgr.resolve_ref(params["end_ref"], page)
            await src.drag_to(dst)
            await mgr.settle()
            return ToolResult.text(await self._snapshot_text(params, target_id))
        if kind == "press" and not params.get("ref"):
            await page.keyboard.press(params.get("key") or "Enter")
            await mgr.settle()
            return ToolResult.text(await self._snapshot_text(params, target_id))
        if kind == "fill" and params.get("fields"):
            await self._fill_fields(page, frame, params["fields"])
            await mgr.settle()
            return ToolResult.text(await self._snapshot_text(params, target_id))

        # ---- ref kinds -----------------------------------------------------
        ref = params.get("ref")
        if not ref:
            return ToolResult.text(f"act kind={kind} requires ref (or fields for fill)", is_error=True)
        loc = mgr.resolve_ref(ref, page)

        try:
            if kind == "click":
                kwargs = {}
                if timeout is not None:
                    kwargs["timeout"] = timeout
                if params.get("button"):
                    kwargs["button"] = params["button"]
                if params.get("modifiers"):
                    kwargs["modifiers"] = self._modifiers(params["modifiers"])
                if params.get("double_click"):
                    await loc.dblclick(**kwargs)
                else:
                    await loc.click(**kwargs)
                await mgr.settle()
            elif kind == "fill":
                await loc.fill(params.get("text") or "", **({"timeout": timeout} if timeout else {}))
            elif kind == "type":
                if params.get("slowly"):
                    await loc.press_sequentially(params.get("text") or "", delay=params.get("delay_ms"))
                else:
                    await loc.fill(params.get("text") or "")
                if params.get("submit"):
                    await loc.press("Enter")
                    await mgr.settle()
            elif kind == "press":
                await loc.press(params.get("key") or "Enter", **({"delay": params["delay_ms"]} if params.get("delay_ms") else {}))
                await mgr.settle()
            elif kind == "select":
                values = params.get("values") or ([params["value"]] if params.get("value") else [])
                await loc.select_option(values, **({"timeout": timeout} if timeout else {}))
            elif kind == "hover":
                await loc.hover(**({"timeout": timeout} if timeout else {}))
            elif kind == "scrollIntoView":
                await loc.scroll_into_view_if_needed(**({"timeout": timeout} if timeout else {}))
                await mgr.settle()
            else:
                return ToolResult.text(f"Unknown act kind: {kind}", is_error=True)
        except Exception as e:  # noqa: BLE001 — fail fast with a recovery hint on timeouts
            if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
                hint = await self._action_failure_hint(page, ref)
                return ToolResult.text(f"act {kind} on '{ref}' failed: {hint}", is_error=True)
            raise

        return ToolResult.text(await self._snapshot_text(params, target_id))

    async def _action_failure_hint(self, page, ref) -> str:
        """Why an action timed out — stale / off-screen / covered — with the recovery step."""
        try:
            loc = self.manager.resolve_ref(ref, page)
            if await loc.count() == 0:
                return (f"ref '{ref}' is stale (no longer on the page) — take a fresh snapshot "
                        f"and use a ref from it.")
            info = await loc.first.evaluate(_COVER_CHECK_JS)
            state = info.get("state")
            if state == "covered":
                return (f"it is covered by <{info.get('by')}> at its click point — dismiss that "
                        f"overlay/popup (or scroll it away), then re-snapshot and retry.")
            if state in ("hidden", "offscreen"):
                return ("it is off-screen/not visible — scroll it into view (act scrollIntoView "
                        "or evaluate window.scrollBy), then re-snapshot and retry.")
            return ("it didn't become clickable in time — the page likely changed; take a fresh "
                    "snapshot and retry.")
        except Exception:  # noqa: BLE001
            return "it didn't become clickable in time — take a fresh snapshot and retry."

    def _modifiers(self, mods) -> list[str]:
        return [_MODIFIER_ALIASES.get(str(m).lower(), str(m)) for m in mods]

    async def _fill_fields(self, page, frame, fields) -> None:
        for f in fields:
            ref = f.get("ref")
            sel = f.get("selector")
            val = f.get("text") if f.get("text") is not None else f.get("value", "")
            if ref:
                loc = self.manager.resolve_ref(ref, page)
            elif sel:
                loc = page.frame_locator(frame).locator(sel) if frame else page.locator(sel)
            else:
                continue
            await loc.fill(val or "")

    async def _wait(self, page, params: dict) -> None:
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

    async def _screenshot(self, params) -> ToolResult:
        mgr = self.manager
        page = mgr.resolve_target(self._target(params))
        shots = Path(self.config.state_dir) / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        ext = "jpeg" if params.get("image_type") == "jpeg" else "png"
        path = shots / f"shot-{int(time.time() * 1000)}.{ext}"
        opts = {"path": str(path), "type": ext}
        if params.get("element") or params.get("ref"):
            loc = (mgr.resolve_ref(params["ref"], page)
                   if params.get("ref") else page.locator(params["element"]))
            await loc.screenshot(**opts)
        else:
            opts["full_page"] = bool(params.get("full_page"))
            await page.screenshot(**opts)
        msg = f"Screenshot saved: {path}"
        if params.get("labels"):  # pair the image with a boxed snapshot for coordinate mapping
            boxed = await mgr.snapshot(targetId=self._target(params), labels=True, max_chars=6000)
            msg += "\n\n" + boxed
        return ToolResult.text(msg)

    async def _upload(self, params) -> ToolResult:
        mgr = self.manager
        page = mgr.resolve_target(self._target(params))
        paths = params.get("paths") or []
        if not paths:
            return ToolResult.text("upload requires paths", is_error=True)
        ref = params.get("input_ref") or params.get("ref")
        sel = params.get("selector") or params.get("element")
        if ref:
            loc = mgr.resolve_ref(ref, page)
        elif sel:
            loc = page.locator(sel)
        else:
            loc = page.locator("input[type=file]")
        await loc.set_input_files(paths)
        await mgr.settle()
        return ToolResult.text(f"Uploaded {len(paths)} file(s).\n\n" + await self._snapshot_text(params))

    async def _tabs(self, action, params) -> ToolResult:
        mgr = self.manager
        # back-compat: action="tabs" with tab_action
        if action == "tabs":
            action = params.get("tab_action") or "list"

        if action == "list":
            return self._json({"tabs": mgr.list_tabs()})

        if action == "open":
            new_page = await mgr.context.new_page()
            mgr._track_page(new_page)
            mgr._label_page(new_page, params.get("label"))
            mgr.active_page = new_page
            if params.get("url"):
                await new_page.goto(params["url"], wait_until="domcontentloaded")
                await mgr.settle()
            handle = mgr.suggested_target_id(new_page)
            snap = await self._snapshot_text(params, handle) if params.get("url") else "Opened blank tab."
            return ToolResult.text(f"[tab {handle}] {snap}")

        # focus / close need a target
        target_id = self._target(params)
        if target_id is None and params.get("tab_index") is not None:
            target_id = str(params["tab_index"])
        try:
            page = mgr.resolve_target(target_id)
        except ValueError as e:
            return ToolResult.text(str(e), is_error=True)

        if action == "focus":
            mgr.active_page = page
            try:
                await page.bring_to_front()
            except Exception:
                pass
            return ToolResult.text(await self._snapshot_text(params, mgr.suggested_target_id(page)))

        if action == "close":
            closing_active = page is mgr.active_page
            handle = mgr.tab_handle(page)
            await page.close()
            if closing_active:
                remaining = mgr.context.pages
                mgr.active_page = remaining[-1] if remaining else await mgr.context.new_page()
            return ToolResult.text(f"Closed tab {handle}.")

        return ToolResult.text(f"Unknown tab action: {action}", is_error=True)

    def _json(self, obj) -> ToolResult:
        return ToolResult.text(json.dumps(obj, indent=2, default=str), details=obj)
