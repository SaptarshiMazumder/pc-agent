"""render_html: turn HTML (raw string, a local file, or a URL) into a PNG or a PDF.

Generic on purpose — HTML can be anything: a slide frame, a report page, a chart, a styled
title card. The output format follows the out_path suffix (.png or .pdf). Uses a headless
browser (Playwright Chromium, falling back to installed Edge).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace


class RenderHtmlTool(Tool):
    name = "render_html"
    description = (
        "Render HTML to a PNG image or a PDF via a headless browser. Source is ONE of: `html` (raw "
        "string), `html_path` (a local .html file), or `url`. The output format follows out_path's "
        "suffix (.png or .pdf). Generic — use it for slide frames, report pages, title cards, "
        "screenshots, anything HTML."
    )
    label = "Render HTML"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "html": {
                "type": "string",
                "description": "Raw HTML string. Provide one of html / html_path / url.",
            },
            "html_path": {"type": "string", "description": "Path to a local .html file."},
            "url": {"type": "string", "description": "A URL to load."},
            "out_path": {
                "type": "string",
                "description": "Output path; .png -> image, .pdf -> PDF. Absolute or relative to workspace.",
            },
            "width": {"type": "integer", "description": "Viewport width px. Default 1920."},
            "height": {"type": "integer", "description": "Viewport height px. Default 1080."},
            "scale": {
                "type": "number",
                "description": "Device scale factor for crisp PNGs. Default 2.",
            },
            "full_page": {
                "type": "boolean",
                "description": "Capture the full scrollable page (PNG). Default false.",
            },
            "wait_ms": {
                "type": "integer",
                "description": "Settle time before capture. Default 250.",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    def _resolve(self, p: str) -> Path:
        path = Path(p)
        if path.is_absolute():
            return path
        ws = current_workspace(str(getattr(self.config, "workspace", "."))) or "."
        return Path(ws) / path

    def _run(self, params: dict) -> dict:
        srcs = [k for k in ("html", "html_path", "url") if params.get(k)]
        if len(srcs) != 1:
            raise ValueError("provide exactly ONE of: html, html_path, url")
        out = self._resolve(params["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        fmt = "pdf" if out.suffix.lower() == ".pdf" else "png"
        width = int(params.get("width", 1920))
        height = int(params.get("height", 1080))
        scale = float(params.get("scale", 2))
        full_page = bool(params.get("full_page", False))
        wait_ms = int(params.get("wait_ms", 250))

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            try:
                br = pw.chromium.launch()
            except Exception:
                br = pw.chromium.launch(channel="msedge")
            try:
                page = br.new_page(
                    viewport={"width": width, "height": height}, device_scale_factor=scale
                )
                if params.get("url"):
                    page.goto(params["url"], wait_until="networkidle")
                elif params.get("html_path"):
                    page.goto(
                        self._resolve(params["html_path"]).resolve().as_uri(),
                        wait_until="networkidle",
                    )
                else:
                    page.set_content(params["html"], wait_until="networkidle")
                page.wait_for_timeout(wait_ms)
                if fmt == "pdf":
                    page.pdf(path=str(out), print_background=True)
                else:
                    page.screenshot(path=str(out), full_page=full_page)
            finally:
                br.close()
        return {"path": str(out), "format": fmt}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"render_html failed: {e}", is_error=True)
        return ToolResult.text(
            f"Rendered HTML -> {r['path']} ({r['format']}).", details=r, artifacts=[r["path"]]
        )  # deliverable: the rendered output
