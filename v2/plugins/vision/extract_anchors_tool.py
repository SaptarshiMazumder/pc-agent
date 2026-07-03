"""extract_anchors: locate named structures in an image and return label-anchor JSON.

The bridge from generated artwork to the vector overlay. Given a finished image and a list of
structures to label, a vision model returns, for each, the PIXEL point to point at and which side
the label should sit — exactly the shape render_overlay's `annotation` element consumes. This is how
the hybrid pipeline places correct labels on art it just generated, without baking text into pixels.

Coordinates are absolute pixels, origin top-left, in the image's own resolution (we read the dims
with PIL and tell the model, so it scales correctly).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace
from agentd.application.tool_models import resolve_tool_model

import vision_gemini as vg

# Ask in Gemini's NATIVE spatial convention — normalized 0-1000, point as [y, x], box as
# [ymin, xmin, ymax, xmax]. This is what the model is trained to emit; fighting it with an
# "absolute pixels [x,y]" request is what made points come back at the wrong scale / axis-swapped.
# We convert 0-1000 -> pixels ourselves in code.
_PROMPT = """Locate scientific/anatomical structures in this image so labels can point at them.
Use the standard normalized image coordinate system: integers 0-1000, origin top-left.

For EACH structure below give:
- "point": [y, x]  — the single best point ON the structure to aim a label leader at
- "box_2d": [ymin, xmin, ymax, xmax]  — its bounding box
- "side": "left" or "right"  — which side the label text should sit
Structures:
{structures}

Return ONLY a JSON array, one object per structure:
[{{"label": "<name>", "point": [y, x], "box_2d": [ymin, xmin, ymax, xmax], "side": "left"|"right", "present": true}}]
All coordinates are integers 0-1000 (NOT pixels). If a structure is not visible, set "present": false."""


def _clamp01k(v):
    try:
        return max(0.0, min(1000.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _to_pixels(raw_anchors, W, H):
    """Convert Gemini's normalized 0-1000 [y,x] point + [ymin,xmin,ymax,xmax] box into our pixel
    convention: target [x_px, y_px] and box [x, y, w, h] px. Point wins; box gives the target when
    no point is returned, and always gives the structure footprint for place_labels."""
    out = []
    for a in raw_anchors:
        present = a.get("present", True)
        box_px = None
        b = a.get("box_2d") or a.get("box")
        if b and len(b) >= 4:
            ymin, xmin, ymax, xmax = (_clamp01k(b[0]), _clamp01k(b[1]), _clamp01k(b[2]), _clamp01k(b[3]))
            if ymax < ymin:
                ymin, ymax = ymax, ymin
            if xmax < xmin:
                xmin, xmax = xmax, xmin
            box_px = [round(xmin / 1000 * W, 1), round(ymin / 1000 * H, 1),
                      round((xmax - xmin) / 1000 * W, 1), round((ymax - ymin) / 1000 * H, 1)]
        p = a.get("point") or a.get("target")
        if p and len(p) >= 2:
            ny, nx = _clamp01k(p[0]), _clamp01k(p[1])
            target = [round(nx / 1000 * W, 1), round(ny / 1000 * H, 1)]
        elif box_px:
            target = [round(box_px[0] + box_px[2] / 2, 1), round(box_px[1] + box_px[3] / 2, 1)]
        else:
            target = [0.0, 0.0]
            present = False
        out.append({"label": a.get("label"), "target": target, "box": box_px,
                    "side": a.get("side"), "present": present})
    return out


def _ink_grid(img_path, cell=6):
    """Coarse boolean grid of where the artwork is drawn (non-white ink), for snapping targets."""
    from PIL import Image
    im = Image.open(img_path).convert("RGBA")
    W, H = im.size
    flat = Image.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, 255)), im).convert("L")
    ink = flat.point(lambda v: 255 if v < 245 else 0)
    cols, rows = max(1, (W + cell - 1) // cell), max(1, (H + cell - 1) // cell)
    small = ink.resize((cols, rows), Image.BILINEAR)
    px = small.load()
    grid = [[px[x, y] > 25 for x in range(cols)] for y in range(rows)]
    return grid, cols, rows, cell, W, H


def _snap_to_ink(grid, cols, rows, cell, target, box, W, H):
    """Move a target onto the nearest DRAWN pixel — constrained to its structure's box (or a window
    around the point if no box) — so a leader dot never lands in empty whitespace. Returns
    (snapped_target, moved_bool)."""
    tx, ty = target
    if box:
        bx, by, bw, bh = box
        gx0, gy0 = max(0, int(bx // cell)), max(0, int(by // cell))
        gx1, gy1 = min(cols - 1, int((bx + bw) // cell)), min(rows - 1, int((by + bh) // cell))
    else:
        win = max(cols, rows) * 0.12                       # search a local window only
        cx, cy = tx / cell, ty / cell
        gx0, gy0 = max(0, int(cx - win)), max(0, int(cy - win))
        gx1, gy1 = min(cols - 1, int(cx + win)), min(rows - 1, int(cy + win))
    if gx1 < gx0 or gy1 < gy0:
        return target, False
    tcx = min(max(int(tx // cell), gx0), gx1)
    tcy = min(max(int(ty // cell), gy0), gy1)
    if grid[tcy][tcx]:
        return target, False                               # already on the structure
    best, best_d = None, 1e18
    for gy in range(gy0, gy1 + 1):
        row = grid[gy]
        for gx in range(gx0, gx1 + 1):
            if row[gx]:
                d = (gx - tcx) ** 2 + (gy - tcy) ** 2
                if d < best_d:
                    best_d, best = d, (gx, gy)
    if best is None:
        return target, False                               # box had no ink (shouldn't happen)
    return [round((best[0] + 0.5) * cell, 1), round((best[1] + 0.5) * cell, 1)], True


class ExtractAnchorsTool(Tool):
    name = "extract_anchors"
    plugin = "vision"
    needs_model = True
    default_model = vg.GROUNDING_MODEL   # Pro = precise localization; config plugins.vision.* overrides
    description = (
        "Locate named structures in an image and return label-anchor JSON for the overlay: for each "
        "structure, the pixel `target` [x,y] to point at, its bounding `box` [x,y,w,h], and a `side` "
        "(left/right) for the label. Grounding runs on a Gemini Pro model in its native normalized "
        "0-1000 coordinate convention (converted to pixels here), and each target is snapped onto the "
        "nearest drawn pixel of its structure — so leader dots land ON the structure, never in empty "
        "whitespace. Feed the result straight into place_labels (it uses the boxes to seat labels just "
        "outside each structure). Input: `image` (path) and `structures` (names). Uses "
        "GEMINI_API_KEY/GOOGLE_API_KEY; works even if the agent's own reasoning model can't see images."
    )
    label = "Extract Anchors"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["image", "structures"],
        "properties": {
            "image": {"type": "string", "description": "Path to the image to analyze."},
            "structures": {"type": "array", "items": {"type": "string"},
                           "description": "Names of the structures to locate and label."},
            "snap": {"type": "boolean", "description": "Snap each target onto the nearest drawn pixel within its structure so dots never float in whitespace. Default true."},
            "model": {"type": "string", "description": f"Override the grounding model (litellm 'provider/model'; bare id => gemini). Resolves per-call > agent.toml/config plugins.vision.tools.extract_anchors > plugins.vision default > {vg.GROUNDING_MODEL} (Pro = precise localization)."},
            "api_key": {"type": "string", "description": "Override key (else GEMINI_API_KEY/GOOGLE_API_KEY)."},
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
        img = self._resolve(params["image"])
        w, h = vg.image_dims(img)
        prompt = _PROMPT.format(structures="\n".join(f"- {s}" for s in params["structures"]))
        model = resolve_tool_model(self.config, self.plugin, self.name,
                                   per_call=params.get("model"), default=vg.GROUNDING_MODEL)
        raw = vg.analyze(img, prompt, model=model, api_key=params.get("api_key"), want_json=True)
        parsed = vg.parse_json(raw)
        if not isinstance(parsed, list):
            raise ValueError(f"expected a JSON array, got {type(parsed).__name__}")

        anchors = _to_pixels(parsed, w, h)                 # normalized 0-1000 -> pixels

        snapped = 0
        if params.get("snap", True):
            grid, cols, rows, cell, W, H = _ink_grid(img)
            for a in anchors:
                if a.get("present", True) and a["target"] != [0.0, 0.0]:
                    a["target"], moved = _snap_to_ink(grid, cols, rows, cell, a["target"],
                                                      a.get("box"), W, H)
                    snapped += int(moved)

        present = [a for a in anchors if a.get("present", True)]
        return {"width": w, "height": h, "anchors": anchors, "found": len(present),
                "snapped": snapped, "requested": len(params["structures"])}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"extract_anchors failed: {e}", is_error=True)
        return ToolResult.text(
            f"Located {r['found']}/{r['requested']} structure(s) in a {r['width']}x{r['height']} image "
            f"({r['snapped']} target(s) snapped onto their structure). Pass anchors[] to place_labels "
            f"(they carry target + box + side).", details=r)
