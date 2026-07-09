"""read_labels_from_image: turn ANY labelled image into editable overlay elements — in the flow OR standalone.

Given a LABELLED image (text labels + leader lines drawn on it), this returns, per label: its text, the
pointer target (leader tip on the structure), and where the label sits (`at`, kept in the margin) — as
ready-to-draw render_editable_overlay `annotation` elements. Because it READS drawn text + lines (OCR-like) rather
than GUESSING where anatomy is, positions are correct by construction.

It always overlays onto a clean TEXTLESS base so nothing doubles up:
  • In the create flow you already HAVE the textless art — pass it as `base`.
  • Standalone (a random labelled image, no base): read_labels_from_image ASKS GEMINI to strip the baked labels/leaders
    off the image, giving a clean textless base to overlay onto — same result, no double text, no erase.

So the tool behaves identically everywhere; the only difference is whether the clean base is handed in or
made on the spot. There is deliberately NO "paint white over the text" step (that whites-out artwork).
Coordinates come back in the base's pixel space, origin top-left.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import vision_gemini as vg

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace
from agentd.application.tool_models import resolve_tool_model

# Ask in Gemini's NATIVE spatial convention — normalized 0-1000, point as [y, x] (converted to pixels here).
_PROMPT = """This scientific figure ALREADY has text labels joined by thin leader lines to structures.
Read them. Use the standard normalized image coordinate system: integers 0-1000, origin top-left.
{expected}
For EACH label drawn on the image give:
- "label": the label text, transcribed VERBATIM (exact spelling/case; join a multi-line label with a space)
- "point": [y, x]  — the point where THIS label's leader line TOUCHES / points at its structure (the target
                     ON THE ARTWORK, NOT where the text sits). If a label has no visible leader, use the
                     nearest point on the structure it names.
- "label_at": [y, x]  — the CENTER of the label's TEXT itself (where the words sit — usually out in the
                     margin). This is where the editable label will be placed, so read it precisely.
- "side": "left" or "right"  — which side of the target the label text sits

Return ONLY a JSON array, one object per label actually drawn:
[{{"label": "<verbatim>", "point": [y, x], "label_at": [y, x], "side": "left"|"right", "present": true}}]
All coordinates are integers 0-1000 (NOT pixels)."""

# The strip prompt — used ONLY when no textless base is provided, to make one from the labelled image.
_STRIP_PROMPT = (
    "Reproduce this EXACT image, pixel-for-pixel, but REMOVE every text label, number, callout, legend and "
    "leader / pointer line COMPLETELY. Keep ALL artwork, anatomy, colours and shading unchanged and sharp, "
    "on a pure solid white background. There must be NO text and NO leader lines anywhere in the output."
)


def _clamp01k(v):
    try:
        return max(0.0, min(1000.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _pt_to_px(p, W, H):
    """Normalized 0-1000 [y,x] -> pixel [x,y], or None if absent/malformed."""
    if not p or len(p) < 2:
        return None
    ny, nx = _clamp01k(p[0]), _clamp01k(p[1])
    return [round(nx / 1000 * W, 1), round(ny / 1000 * H, 1)]


def _to_pixels(raw_anchors, W, H):
    """Read the pointer target + the label position, both in pixels. `target` = leader endpoint on the
    structure; `at` = where the label text sits (usually the margin)."""
    out = []
    for a in raw_anchors:
        target = _pt_to_px(a.get("point") or a.get("target"), W, H)
        at = _pt_to_px(a.get("label_at") or a.get("at"), W, H)
        present = a.get("present", True) and target is not None
        side = a.get("side") if a.get("side") in ("left", "right") else None
        out.append(
            {
                "label": a.get("label"),
                "target": target or [0.0, 0.0],
                "at": at,
                "side": side,
                "present": present,
            }
        )
    return out


def _ink_grid(img_path, cell=6):
    """Coarse boolean grid of where the artwork is drawn (non-white ink), for snapping pointer targets."""
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
    """Move a pointer target onto the nearest DRAWN pixel (within a local window when no box), so a
    leader dot never lands in empty whitespace. Returns (snapped_target, moved_bool)."""
    tx, ty = target
    if box:
        bx, by, bw, bh = box
        gx0, gy0 = max(0, int(bx // cell)), max(0, int(by // cell))
        gx1, gy1 = min(cols - 1, int((bx + bw) // cell)), min(rows - 1, int((by + bh) // cell))
    else:
        win = max(cols, rows) * 0.12  # search a local window only
        cx, cy = tx / cell, ty / cell
        gx0, gy0 = max(0, int(cx - win)), max(0, int(cy - win))
        gx1, gy1 = min(cols - 1, int(cx + win)), min(rows - 1, int(cy + win))
    if gx1 < gx0 or gy1 < gy0:
        return target, False
    tcx = min(max(int(tx // cell), gx0), gx1)
    tcy = min(max(int(ty // cell), gy0), gy1)
    if grid[tcy][tcx]:
        return target, False  # already on the structure
    best, best_d = None, 1e18
    for gy in range(gy0, gy1 + 1):
        row = grid[gy]
        for gx in range(gx0, gx1 + 1):
            if row[gx]:
                d = (gx - tcx) ** 2 + (gy - tcy) ** 2
                if d < best_d:
                    best_d, best = d, (gx, gy)
    if best is None:
        return target, False
    return [round((best[0] + 0.5) * cell, 1), round((best[1] + 0.5) * cell, 1)], True


class ReadLabelsTool(Tool):
    name = "read_labels_from_image"
    plugin = "vision"
    needs_model = True
    model_kind = "vision"  # READS an image — needs a multimodal (vision) model
    default_model = vg.GROUNDING_MODEL  # Pro = precise reading; config plugins.vision.* overrides
    description = (
        "Turn a LABELLED image (labels + leaders drawn on it) into ready-to-draw overlay `annotation` "
        "elements: per label, its `text`, the pixel `target` [x,y] where its leader points at the structure, "
        "and `at` [x,y] where the label sits (kept where drawn — the margin). Works on ANY labelled image, "
        "in the create flow OR standalone. It always overlays onto a clean TEXTLESS base: pass `base`=your "
        "textless art if you have it; if you DON'T, read_labels_from_image asks Gemini to STRIP the baked labels off the "
        "image to make one (no double text, no white-out erase) and returns it as `base_png`. Feed the "
        "returned `elements` to render_editable_overlay over `base_png`, then compose_figure_layers. Because it READS drawn "
        "labels (reliable) instead of GUESSING anatomy, positions are correct by construction. Uses "
        "GEMINI_API_KEY; works even when the agent's own model can't see images."
    )
    label = "Read Labels"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["image"],
        "properties": {
            "image": {"type": "string", "description": "Path to the LABELLED image to read."},
            "base": {
                "type": "string",
                "description": "Optional path to the TEXTLESS base to overlay on (your generated textless art). Anchors come back in ITS space, pointer snapped to its ink. OMIT for a standalone labelled image and read_labels_from_image will strip the labels off `image` via Gemini to make a clean base (returned as `base_png`).",
            },
            "snap": {
                "type": "boolean",
                "description": "Snap each pointer target onto the base's nearest drawn pixel so dots land on the structure. Default true. (The label position `at` is never snapped — it stays in the margin.)",
            },
            "structures": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: the labels you expect to be drawn — steers the read and lets you match them to your intended set.",
            },
            "model": {
                "type": "string",
                "description": f"Override the reading model (litellm 'provider/model'; bare id => gemini). Resolves per-call > agent.toml/config plugins.vision.tools.read_labels_from_image > plugins.vision default > {vg.GROUNDING_MODEL}.",
            },
            "api_key": {
                "type": "string",
                "description": "Override key (else GEMINI_API_KEY/GOOGLE_API_KEY).",
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

    def _strip_labels(self, labelled: Path, api_key) -> Path:
        """No textless base given: ask Gemini (Nano Banana) to remove the baked labels/leaders from the
        labelled image -> a clean textless base. This is how read_labels_from_image works standalone on ANY labelled
        image (never overlays onto baked text; no whitening/erase)."""
        import sys as _sys

        igdir = str(
            Path(__file__).resolve().parent.parent / "figure-art"
        )  # figure-art is a sibling plugin
        if igdir not in _sys.path:
            _sys.path.insert(0, igdir)
        import figure_art_gemini as ig

        model = resolve_tool_model(
            self.config, "figure-art", "generate_artwork", default=ig.DEFAULT_MODEL
        )
        key = ig.resolve_key(api_key, self.config)
        out = labelled.with_name(labelled.stem + "_textless.png")
        ig.generate_image(_STRIP_PROMPT, out, model=model, api_key=key, reference_images=[labelled])
        return out

    def _run(self, params: dict) -> dict:
        img = self._resolve(params["image"])
        # A clean TEXTLESS base to overlay editable labels onto. In the flow, pass `base`=your textless art;
        # standalone, strip the labels off `image` via Gemini so we still get a clean base (no double text).
        stripped = False
        if params.get("base"):
            base = self._resolve(params["base"])
        else:
            try:
                base = self._strip_labels(img, params.get("api_key"))
                stripped = True
            except Exception as e:
                raise RuntimeError(
                    f"read_labels_from_image needs a TEXTLESS base to overlay onto, and auto-stripping the labels off "
                    f"{img.name} via Gemini failed: {e}. Pass `base`=a textless version of the image."
                )
        Wc, Hc = vg.image_dims(base)

        expected = ""
        if params.get("structures"):
            expected = (
                "Expected labels (read these; omit any not actually drawn):\n"
                + "\n".join(f"- {s}" for s in params["structures"])
                + "\n"
            )
        prompt = _PROMPT.format(expected=expected)
        model = resolve_tool_model(
            self.config,
            self.plugin,
            self.name,
            per_call=params.get("model"),
            default=vg.GROUNDING_MODEL,
        )
        raw = vg.analyze(img, prompt, model=model, api_key=params.get("api_key"), want_json=True)
        parsed = vg.parse_json(raw)
        if not isinstance(parsed, list):
            raise ValueError(f"expected a JSON array, got {type(parsed).__name__}")
        anchors = _to_pixels(parsed, Wc, Hc)

        # Snap the POINTER target onto the base's drawn ink (never the label position `at`).
        snapped = 0
        if params.get("snap", True):
            grid, cols, rows, cell, W, H = _ink_grid(base)
            for a in anchors:
                if a.get("present", True) and a["target"] != [0.0, 0.0]:
                    a["target"], moved = _snap_to_ink(
                        grid, cols, rows, cell, a["target"], None, W, H
                    )
                    snapped += int(moved)

        # Build render_editable_overlay `annotation` elements: editable label AT the model's label position, with a
        # leader to the pointer target. If the model didn't give a label position, park it in the margin.
        elements = []
        for a in anchors:
            if not a.get("present", True) or a["target"] == [0.0, 0.0]:
                continue
            at = a.get("at")
            if not at:
                mx = 0.06 * Wc if a.get("side") == "left" else 0.94 * Wc
                at = [round(mx, 1), a["target"][1]]
            el = {
                "kind": "annotation",
                "text": a.get("label") or "",
                "target": a["target"],
                "at": at,
            }
            if a.get("side"):
                el["side"] = a["side"]
            elements.append(el)

        present = [a for a in anchors if a.get("present", True)]
        return {
            "width": Wc,
            "height": Hc,
            "anchors": anchors,
            "elements": elements,
            "base_png": str(base),
            "stripped": stripped,
            "found": len(present),
            "snapped": snapped,
        }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"read_labels_from_image failed: {e}", is_error=True)
        made = (
            f" Made a clean textless base by stripping the labels -> {r['base_png']}."
            if r.get("stripped")
            else ""
        )
        snap = f" {r['snapped']} pointer(s) snapped onto the base." if r.get("snapped") else ""
        return ToolResult.text(
            f"Read {r['found']} label(s) into overlay elements (base {r['width']}x{r['height']}).{made}{snap} "
            f"Draw `elements` with render_editable_overlay over the base (`base_png`), then compose_figure_layers.",
            details=r,
        )
