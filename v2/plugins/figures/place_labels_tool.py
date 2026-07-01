"""place_labels: turn anchor points into clean, non-overlapping label callouts.

The placement brain between extract_anchors and render_overlay. Given the structures' target points,
it positions each label in the side margin as close to its structure as possible without colliding
(PAVA de-overlap), edge-aligned, with a short-stub leader — then hands back render_overlay-ready
`elements`. Use it so leaders stay short and labels never overlap, instead of hand-placing them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace
import figures_labels


def _occupancy_from_artwork(path: Path, cell: int = 16, threshold: float = 0.10) -> dict:
    """Coarse boolean grid of where the ARTWORK is drawn, from its white background. Each cell is
    `cell`x`cell` px; a cell is 'occupied' when more than `threshold` of it is non-white ink. This is
    what lets the placer drop labels into real whitespace instead of over the drawing."""
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    W, H = im.size
    flat = Image.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, 255)), im).convert("L")
    ink = flat.point(lambda v: 255 if v < 245 else 0)              # non-white => ink
    cols, rows = max(1, (W + cell - 1) // cell), max(1, (H + cell - 1) // cell)
    small = ink.resize((cols, rows), Image.BILINEAR)               # avg ink fraction per cell
    cut = int(255 * threshold)
    px = small.load()
    grid = [[1 if px[x, y] > cut else 0 for x in range(cols)] for y in range(rows)]
    return {"cell": cell, "grid": grid, "width": W, "height": H}


class PlaceLabelsTool(Tool):
    name = "place_labels"
    description = (
        "Position labels for a figure WITHOUT overlapping, returning render_overlay-ready `elements` "
        "(label + leader pairs). Default `strategy`='auto' places each label in the best free spot in "
        "a ring AROUND its own structure (above/below/beside — wherever there's room), with a short "
        "non-crossing leader; it only spills to a side margin when the neighbourhood is occupied. Pass "
        "`artwork` (the figure PNG) so it avoids drawing labels over the illustration (whitespace "
        "mask), and `anchors` may carry a `box`[x,y,w,h] structure footprint so labels sit just "
        "outside each structure. Other strategies: 'adjacent' (ring only, never spill) and 'callout' "
        "(classic two-column margins). Use this between extract_anchors and render_overlay."
    )
    label = "Place Labels"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["width", "height", "anchors"],
        "properties": {
            "width": {"type": "integer", "description": "Canvas width px (match the artwork)."},
            "height": {"type": "integer", "description": "Canvas height px (match the artwork)."},
            "anchors": {
                "type": "array",
                "description": "From extract_anchors: each {label, target:[x,y], side?, box?:[x,y,w,h] structure footprint}.",
                "items": {"type": "object", "required": ["label", "target"],
                          "properties": {"label": {"type": "string"},
                                         "target": {"type": "array", "items": {"type": "number"}},
                                         "side": {"type": "string", "enum": ["left", "right"]},
                                         "box": {"type": "array", "items": {"type": "number"},
                                                 "description": "Optional structure bbox [x,y,w,h]."}}},
            },
            "strategy": {"type": "string", "enum": ["auto", "adjacent", "callout"],
                         "description": "auto (ring near structures + margin spill, default), adjacent (ring only), callout (two-column margins)."},
            "artwork": {"type": "string", "description": "Optional figure PNG path; used to avoid placing labels over the drawing (whitespace mask)."},
            "leader_head": {"type": "string", "enum": ["none", "standard", "soft"],
                            "description": "Arrowhead at the leader's target end instead of a dot. Default none (dot)."},
            "font_size": {"type": "integer", "description": "Label font px. Default 15."},
            "inset": {"type": "number", "description": "Margin from the canvas edge to a spilled label box. Default 28."},
            "gap": {"type": "number", "description": "Minimum vertical gap between labels. Default font_size+16."},
            "stub": {"type": "number", "description": "Horizontal leader stub length (callout strategy). Default 22."},
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
        anchors = [a for a in params["anchors"] if a.get("present", True)]   # drop not-visible structures
        # normalise an anchor `box` into the engine's `box_region`
        for a in anchors:
            if a.get("box") is not None and "box_region" not in a:
                a["box_region"] = a["box"]
        occ = None
        if params.get("artwork"):
            occ = _occupancy_from_artwork(self._resolve(params["artwork"]))
        opts = {k: params[k] for k in ("font_size", "inset", "gap", "stub", "strategy", "leader_head")
                if k in params}
        return figures_labels.place(int(params["width"]), int(params["height"]), anchors,
                                    occupancy=occ, **opts)

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"place_labels failed: {e}", is_error=True)
        n = len(r["placements"])
        near = sum(1 for p in r["placements"] if p.get("kind") == "ring")
        return ToolResult.text(
            f"Placed {n} label(s) without overlap ({near} hugging their structure, {n - near} in "
            f"margins); pass `elements` to render_overlay (canvas {r['width']}x{r['height']}).",
            details=r)
