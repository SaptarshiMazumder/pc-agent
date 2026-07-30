"""render_svg: rasterize an SVG to a PNG via headless Chromium, and hand the image BACK to the
agent so it can LOOK at what it drew.

This closes the figure feedback loop: the agent emits SVG -> render_svg -> the PNG comes back as an
ImageContent block the vision model sees -> the agent spots an overflowing label / mis-aimed arrow
and fixes the source. Browser rendering (not cairosvg) means gradients, <marker> heads, and
feDropShadow/feGaussianBlur filters look exactly as in a browser/Illustrator.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from figures_common import resolve_path, svg_size, render_svg_to_png, image_result


class RenderSvgTool(Tool):
    name = "render_svg"
    description = (
        "Rasterize an SVG to a PNG with a headless browser (so gradients, arrow markers, and "
        "blur/shadow filters render faithfully). Source is `svg` (raw) or `svg_path`. Width/height "
        "default to the SVG's own size. `background` defaults to TRANSPARENT (pass a CSS colour like "
        "'#FFFFFF' for an opaque figure). Returns the PNG path AND the image itself so you can SEE "
        "the result and fix the source if a label overflows or an arrow is mis-aimed."
    )
    label = "Render SVG"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "svg": {"type": "string", "description": "Raw SVG markup. Provide this OR svg_path."},
            "svg_path": {"type": "string", "description": "Path to a .svg file."},
            "out_path": {"type": "string", "description": "Output .png path (absolute or workspace-relative)."},
            "width": {"type": "integer", "description": "Render width px (default: SVG's width)."},
            "height": {"type": "integer", "description": "Render height px (default: SVG's height)."},
            "scale": {"type": "number", "description": "Device scale factor for crispness. Default 2."},
            "background": {"type": "string", "description": "CSS colour for an opaque PNG. Omit for transparent."},
        },
    }

    def __init__(self, config):
        self.config = config

    def _run(self, params: dict) -> dict:
        if bool(params.get("svg")) == bool(params.get("svg_path")):
            raise ValueError("provide exactly ONE of: svg, svg_path")
        text = (resolve_path(self.config, params["svg_path"]).read_text(encoding="utf-8")
                if params.get("svg_path") else params["svg"])
        w0, h0 = svg_size(text)
        width = int(params.get("width") or w0)
        height = int(params.get("height") or h0)
        out = resolve_path(self.config, params["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        render_svg_to_png(text, out, width, height,
                          scale=float(params.get("scale", 2)),
                          background=params.get("background"))
        return {"path": str(out), "width": width, "height": height,
                "scale": float(params.get("scale", 2))}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"render_svg failed: {e}", is_error=True)
        return image_result(f"Rendered SVG -> {r['path']} ({r['width']}x{r['height']} @ {r['scale']}x).",
                            Path(r["path"]), r)
