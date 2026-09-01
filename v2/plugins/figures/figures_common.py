"""Shared helpers for the figures plugin.

Kept separate from the Tool classes so the heavy bits (Playwright, Pillow) are imported lazily
inside functions — a tool that only validates SVG never pays for a browser launch. Nothing here
imports agentd EXCEPT the tiny workspace helper, so the geometry/render code stays unit-testable.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from agent_runtime.application.run_context import current_workspace
from agent_runtime.domain.messages import ImageContent, TextContent
from agent_runtime.application.interfaces.tool import ToolResult


def resolve_path(config, p: str) -> Path:
    """Resolve a tool path arg: absolute as-is, else relative to the agent's per-run workspace
    (falling back to the global config workspace). Mirrors how html/plantuml resolve paths so
    figure outputs land in the same place as the rest of an agent's work."""
    path = Path(p)
    if path.is_absolute():
        return path
    ws = current_workspace(str(getattr(config, "workspace", "."))) or "."
    return Path(ws) / path


def png_block(path: Path) -> ImageContent:
    """Read a PNG off disk into an ImageContent block so the AGENT can SEE the result.
    The core forwards tool-returned images to the vision model (litellm), which is what makes
    the render -> look -> fix feedback loop possible without any extra plumbing."""
    data = path.read_bytes()
    return ImageContent(data=base64.b64encode(data).decode(), mime_type="image/png")


def image_result(text: str, path: Path, details: dict) -> ToolResult:
    """A ToolResult that carries BOTH a text summary (path + dims, for the transcript) and the
    rendered PNG (for the model's eye)."""
    return ToolResult(content=[TextContent(text=text), png_block(path)], details=details)


# --- SVG size parsing -------------------------------------------------------
_LEN = re.compile(r"[-+]?[0-9]*\.?[0-9]+")


def _num(s: str | None):
    if not s:
        return None
    m = _LEN.search(str(s))
    return float(m.group()) if m else None


def svg_size(svg_text: str) -> tuple[int, int]:
    """Best-effort (width, height) in px for an SVG string: prefer width/height attrs, fall back
    to the viewBox's w/h, default 1920x1080. Used so render_svg can size its viewport without the
    caller having to repeat dimensions already declared in the markup."""
    head = svg_text[:2000]
    w = _num(re.search(r'\bwidth\s*=\s*["\']([^"\']+)', head) and
             re.search(r'\bwidth\s*=\s*["\']([^"\']+)', head).group(1))
    h = _num(re.search(r'\bheight\s*=\s*["\']([^"\']+)', head) and
             re.search(r'\bheight\s*=\s*["\']([^"\']+)', head).group(1))
    if not (w and h):
        vb = re.search(r'viewBox\s*=\s*["\']([^"\']+)', head)
        if vb:
            parts = _LEN.findall(vb.group(1))
            if len(parts) == 4:
                w = w or float(parts[2])
                h = h or float(parts[3])
    return int(round(w or 1920)), int(round(h or 1080))


# --- Playwright SVG -> PNG --------------------------------------------------
def render_svg_to_png(svg_text: str, out_png: Path, width: int, height: int,
                      scale: float = 2.0, background: str | None = None) -> None:
    """Rasterize an SVG string to a PNG via headless Chromium (msedge fallback), exactly the
    renderer the html plugin / build.py use — so gradients, <marker>s, and feDropShadow/feGaussianBlur
    filters come out pixel-identical to a real browser (cairosvg would drop several of those).

    background=None -> a TRANSPARENT PNG (the overlay case, so it can sit on artwork). Pass a CSS
    colour (e.g. "#FAFAFA") for an opaque figure. Output is width*scale by height*scale px.
    """
    bg = background or "transparent"
    html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<style>*{{margin:0;padding:0;box-sizing:border-box}}html,body{{background:{bg}}}</style>'
        f'</head><body>{svg_text}</body></html>'
    )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        try:
            br = pw.chromium.launch()
        except Exception:
            br = pw.chromium.launch(channel="msedge")
        try:
            page = br.new_page(viewport={"width": width, "height": height},
                               device_scale_factor=scale)
            page.set_content(html, wait_until="networkidle")
            page.wait_for_timeout(150)
            page.screenshot(path=str(out_png), omit_background=(background is None),
                            clip={"x": 0, "y": 0, "width": width, "height": height})
        finally:
            br.close()
