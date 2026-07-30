"""export_pdf: render an SVG (or a layered figure SVG) to a VECTOR PDF via headless Chromium.

Publication deliverable: Chromium prints SVG as real vector paths/text in the PDF (not a raster
screenshot), so labels stay selectable and the figure scales cleanly. One page, sized to the SVG, no
margins. Reuses the same browser the html/figures plugins use — no new dependency.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from figexport_common import resolve_path

_LEN = re.compile(r"[-+]?[0-9]*\.?[0-9]+")


def _svg_size(svg: str):
    head = svg[:2000]
    mw = re.search(r'\bwidth\s*=\s*["\']([^"\']+)', head)
    mh = re.search(r'\bheight\s*=\s*["\']([^"\']+)', head)
    def num(m):
        return float(_LEN.search(m.group(1)).group()) if m and _LEN.search(m.group(1)) else None
    w, h = num(mw), num(mh)
    if not (w and h):
        vb = re.search(r'viewBox\s*=\s*["\']([^"\']+)', head)
        if vb:
            p = _LEN.findall(vb.group(1))
            if len(p) == 4:
                w, h = w or float(p[2]), h or float(p[3])
    return int(round(w or 1920)), int(round(h or 1080))


class ExportPdfTool(Tool):
    name = "export_pdf"
    description = (
        "Render an SVG to a VECTOR PDF (selectable text, scalable paths) via headless Chromium — for "
        "publication output. Source is `svg` (raw) or `svg_path` (e.g. the layered figure SVG from "
        "compose_figure_layers). One page sized to the SVG, no margins. Input also `out_path` (.pdf)."
    )
    label = "Export PDF"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "svg": {"type": "string", "description": "Raw SVG markup. Provide this OR svg_path."},
            "svg_path": {"type": "string", "description": "Path to a .svg file."},
            "out_path": {"type": "string", "description": "Output .pdf path."},
        },
    }

    def __init__(self, config):
        self.config = config

    def _run(self, params: dict) -> dict:
        if bool(params.get("svg")) == bool(params.get("svg_path")):
            raise ValueError("provide exactly ONE of: svg, svg_path")
        svg = (resolve_path(self.config, params["svg_path"]).read_text(encoding="utf-8")
               if params.get("svg_path") else params["svg"])
        w, h = _svg_size(svg)
        out = resolve_path(self.config, params["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        html = (f'<!doctype html><meta charset="utf-8">'
                f'<style>@page{{size:{w}px {h}px;margin:0}}*{{margin:0;padding:0}}'
                f'html,body{{width:{w}px;height:{h}px}}</style>{svg}')
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            try:
                br = pw.chromium.launch()
            except Exception:
                br = pw.chromium.launch(channel="msedge")
            try:
                page = br.new_page()
                page.set_content(html, wait_until="networkidle")
                page.wait_for_timeout(150)
                page.pdf(path=str(out), width=f"{w}px", height=f"{h}px",
                         print_background=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
            finally:
                br.close()
        return {"out_path": str(out), "width": w, "height": h}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"export_pdf failed: {e}", is_error=True)
        return ToolResult.text(f"Vector PDF -> {r['out_path']} ({r['width']}x{r['height']}px, 1 page).",
                               details=r, artifacts=[r["out_path"]])  # deliverable: the PDF
