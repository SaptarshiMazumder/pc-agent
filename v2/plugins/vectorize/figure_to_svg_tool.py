"""figure_to_svg — ONE-CALL, self-contained: any labelled figure PNG -> editable layered SVG.

Give it an image, get back an editable SVG. It is STATELESS — no manifest, no prior pipeline step,
no skill orchestration required — so ANY agent can call it in the same flow, a new message, or a
brand-new chat, and it just works. The whole "make this editable" pipeline in a single tool:

  1. strip the labels off with the image model  -> a clean textless base
  2. OCR the original                            -> crisp editable <text> at each label's position
  3. diff original vs base                        -> the annotations-only layer (arrows/lines)
  4. lines/arrows: DETERMINISTIC (no LLM). Each diff component is traced into ONE clean line straight
     from its own pixels — the skeleton diameter gives one start + one end, and the width profile
     flags an arrowhead. The line lands EXACTLY on the ink. A filled shape (dot/marker) is blob-traced.
  5. artwork: remove the background, split it into per-object <image> pieces (each selectable), OR
     (`artwork_mode`='vector') vtrace the whole artwork
  6. compose everything -> one editable SVG (+ a flattened PNG to look at)

Deterministic pixel work reuses vectorize_extract; text reuses the extract_annotations OCR; the
overlay + compose reuse the figures plugin. Needs numpy+scikit-image+rapidocr+vtracer
(pip install 'agentd[figures-vector]') and a Gemini key for the strip only (line tracing is local).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import vectorize_extract as vx
from extract_annotations_tool import _ocr_lines, _quad_bbox, _text_color, strip_labels

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.domain.messages import TextContent


def _sibling(plugin: str):
    """Put a sibling plugin dir on sys.path so its bare-name modules import (runtime already does
    this; belt-and-braces for direct use/tests)."""
    d = str(Path(__file__).resolve().parent.parent / plugin)
    if d not in sys.path:
        sys.path.insert(0, d)


def _stroke_to_line_element(tr, color):
    """A trace_stroke result -> ONE clean overlay element: a leader, or an arrow if a head was
    detected. route 'straight' with the RDP-simplified centerline = a faithful polyline that lands
    exactly on the ink (2 points => a plain line; more => follows the curve). Thin, matched to the
    traced stroke width — so it is never the engine's heavy default arrow."""
    pts = vx.douglas_peucker(tr["points"], max(1.5, tr.get("width", 2.0) * 0.8))
    if tr.get("head_start") and not tr.get("head_end"):
        pts = list(reversed(pts))  # put the arrowhead at the END
    pts = [[round(x, 1), round(y, 1)] for x, y in pts]
    w = round(min(max(tr.get("width", 1.6), 1.0), 3.0), 1)
    if tr.get("head_start") or tr.get("head_end"):
        el = {
            "kind": "arrow", "points": pts, "route": "straight", "color": color,
            "body": "stroked", "head": "standard", "width": w,
            "head_size": round(min(max(w * 3.0, 6.0), 11.0), 1),
        }
        if tr.get("head_start") and tr.get("head_end"):
            el["start_head"] = "standard"
        return el
    return {"kind": "leader", "points": pts, "route": "straight", "color": color, "dot": False, "width": w}


class FigureToSvgTool(Tool):
    name = "figure_to_svg"
    plugin = "vectorize"
    description = (
        "ONE CALL: convert a labelled figure PNG into an editable layered SVG. Self-contained and "
        "STATELESS — hand it any image, get back editable SVG + a preview PNG; works in any flow, "
        "new message, or new chat, no prior steps needed. Internally: strips the labels (image "
        "model) -> textless base; OCRs the text -> editable <text>; diffs -> the arrows/lines and "
        "traces each DETERMINISTICALLY from its pixels (skeleton diameter -> one start+one end, "
        "arrowhead by width; a filled marker is blob-traced) so lines land exactly on the ink, no "
        "LLM guessing; keeps the artwork as a crisp raster by default (`artwork_mode`='vector' to "
        "vtrace it too). Params: `image` (required), `base` (optional textless base to skip the "
        "strip), `artwork_mode` raster|vector, `out_svg`, `out_png`. Use this for 'make it editable "
        "/ convert to SVG / vectorize this figure'. For a plain non-figure logo use trace_image. "
        "Needs numpy+scikit-image+rapidocr+vtracer (pip install 'agentd[figures-vector]')."
    )
    label = "Figure to SVG"
    concurrency = "parallel"
    # Self-declared canvas action: a "Convert to Vector" button on PNG artifacts. The client renders
    # it only for image/png and only when this tool is installed (it ships with figure-creator).
    artifact_action = {"mime": ["image/png"], "label": "Convert to Vector", "param": "image"}
    parameters = {
        "type": "object",
        "required": ["image"],
        "properties": {
            "image": {"type": "string", "description": "Path to the labelled figure (PNG/JPG)."},
            "base": {
                "type": "string",
                "description": "Optional pixel-aligned TEXTLESS base to skip the image-model strip.",
            },
            "artwork_mode": {
                "type": "string",
                "enum": ["raster", "vector"],
                "description": "raster (default) = keep the artwork as a crisp embedded image (highest quality); vector = vtrace the artwork into editable shapes too (fully editable, larger, softer look).",
            },
            "remove_bg": {
                "type": "boolean",
                "description": "Remove the background so the artwork is cut out (transparent) in the SVG instead of sitting on a white rectangle (default true). Uses rembg if installed, else deterministic white-background removal. Positions are unchanged.",
            },
            "out_svg": {
                "type": "string",
                "description": "Output .svg (default <image>_editable.svg).",
            },
            "out_png": {
                "type": "string",
                "description": "Preview .png (default <image>_editable.png).",
            },
            "diff_threshold": {
                "type": "integer",
                "description": "Per-channel diff threshold 0-255 for the annotation layer. Default 40.",
            },
            "force": {
                "type": "boolean",
                "description": "Re-convert even if an up-to-date editable SVG already exists. Default false = return the cached SVG instantly (no model calls) so re-opening a converted image is free.",
            },
            "api_key": {"type": "string", "description": "Override Gemini key (else env)."},
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

    # ---- the whole pipeline ----------------------------------------------------------------
    def _run(self, params: dict) -> dict:
        vx._require_deps()
        import numpy as np
        from PIL import Image

        image = self._resolve(params["image"])
        if not image.is_file():
            raise FileNotFoundError(f"no such image: {image}")
        stem = image.stem
        out_svg = self._resolve(
            params.get("out_svg") or str(image.with_name(stem + "_editable.svg"))
        )
        out_png = self._resolve(
            params.get("out_png") or str(image.with_name(stem + "_editable.png"))
        )
        overlay_path = image.with_name(stem + "_overlay.svg")

        # 0. ALREADY CONVERTED? If the editable SVG exists and is at least as new as the source,
        # return it as-is — no strip / OCR / model calls, no cost. This is what makes "click the PNG
        # again" instantly re-open the SAME vector (idempotent, like FigureLabs). `force:true` redoes it.
        if (
            not params.get("force")
            and out_svg.is_file()
            and out_svg.stat().st_mtime >= image.stat().st_mtime
        ):
            with Image.open(image) as im:
                cw, ch = im.size
            return {
                "out_svg": str(out_svg),
                "out_png": str(out_png) if out_png.is_file() else None,
                "base_png": str(out_svg),
                "overlay_svg": str(overlay_path) if overlay_path.is_file() else str(out_svg),
                "width": cw,
                "height": ch,
                "diff_fraction": 0.0,
                "labels": 0,
                "lines": 0,
                "blob_shapes": 0,
                "cleaned_labels": 0,
                "strip_failed": False,
                "artwork_mode": params.get("artwork_mode", "raster"),
                "stripped": False,
                "cached": True,
            }

        # 1. textless base (strip) — the one image-model call, unless a base is supplied
        stripped = False
        if params.get("base"):
            base_path = self._resolve(params["base"])
        else:
            base_path = strip_labels(self.config, image, params.get("api_key"))
            stripped = True

        L = Image.open(image).convert("RGB")
        B = Image.open(base_path).convert("RGB")
        if B.size != L.size:
            B = B.resize(L.size, Image.LANCZOS)
        W, H = L.size
        La, Ba = np.asarray(L), np.asarray(B)
        s = max(1.0, max(W, H) / 1024.0)

        mask, frac = vx.diff_mask(La, Ba, thr=int(params.get("diff_threshold", 40)))

        # 2. text -> editable labels (OCR the LABELLED image directly; filter artwork misreads)
        elements: list[dict] = []
        label_boxes = []
        for ln in _ocr_lines(image):
            bx0, by0, bx1, by1 = _quad_bbox(ln["quad"])
            if by1 - by0 > 0.14 * H or bx1 - bx0 > 0.9 * W:
                continue
            if float(ln.get("score", 1.0)) < 0.4 or not ln["text"].strip():
                continue
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            fs = min(max((by1 - by0) * 0.9, 8.0), 44.0)
            elements.append(
                {
                    "kind": "label",
                    "text": ln["text"],
                    "x": round(cx, 1),
                    "y": round(cy + fs * 0.32, 1),
                    "anchor": "middle",
                    "font_size": round(fs / s, 1),
                    "color": _text_color(La, (bx0, by0, bx1, by1)),
                    "weight": "500",
                }
            )
            label_boxes.append((bx0, by0, bx1, by1))

        # 2b. DID THE STRIP ACTUALLY STRIP? The image model often just reproduces the figure
        # (labels + lines intact) instead of removing them. When that happens the base still has
        # every line, so diffing finds noise and we'd draw VECTOR lines ON TOP of the baked raster
        # lines -> double lines (the bug). Detect it: how many label boxes are still present in the
        # base? If most are, the strip FAILED.
        def _still_present(box) -> bool:
            x0, y0, x1, y1 = (int(v) for v in box)
            lb = La[y0:y1, x0:x1].astype("int16")
            bb = Ba[y0:y1, x0:x1].astype("int16")
            return lb.size > 0 and float(np.abs(lb - bb).mean()) < 12

        strip_failed = bool(label_boxes) and sum(
            _still_present(b) for b in label_boxes
        ) >= 0.6 * len(label_boxes)

        # 2c. Build the artwork base with the LABEL TEXT erased (so editable <text> never doubles),
        # and decide whether we can safely extract vector arrows:
        #  • strip FAILED  -> the base is the original with labels/lines intact. Erase text from the
        #    ORIGINAL and keep the lines as ARTWORK (raster, or traced once in vector mode). Draw NO
        #    separate vector lines — that is what caused the double lines.
        #  • strip OK      -> clean the strip's residual text and extract the arrows from the diff
        #    (the base has no lines, so vector arrows can't double).
        if strip_failed:
            art_arr, n_cleaned = vx.clean_base(La, La, label_boxes)
            comps = []
            arr_mask = None
        else:
            art_arr, n_cleaned = vx.clean_base(La, Ba, label_boxes)
            arr_mask = mask.copy()
            for x0, y0, x1, y1 in label_boxes:
                arr_mask[
                    max(0, int(y0) - 2) : min(H, int(y1) + 2),
                    max(0, int(x0) - 2) : min(W, int(x1) + 2),
                ] = False
            comps = vx.components(arr_mask)

        # 2d. REMOVE THE BACKGROUND (default): cut the white behind the artwork away so the composed
        # SVG shows the artwork transparently (cleanly-cut borders), not on a white rectangle.
        # Positions are unchanged — only alpha is added. rembg if installed, else deterministic
        # border-connected white removal (keeps white enclosed inside the artwork).
        bg_removed = False
        if params.get("remove_bg", True):
            try:
                art_arr = vx.remove_background(art_arr)
                bg_removed = True
            except Exception:  # never block the conversion on bg removal
                bg_removed = False
        base_path = image.with_name(stem + "_base_clean.png")
        Image.fromarray(art_arr).save(base_path)

        # 3. lines/arrows: DETERMINISTIC (no LLM). Trace each diff-mask component into ONE clean line
        # straight from its own pixels — skeleton diameter gives ONE start + ONE end, arrowheads come
        # from the width profile. So every line lands EXACTLY on the ink instead of a model's guess
        # (that guessing was the 'lines drawn in random places' bug). A component that is a FILLED
        # shape (dot/marker) traces to nothing -> blob-trace it instead. Only runs on a good strip; on
        # a failed strip comps is empty and the lines stay baked in the raster artwork.
        n_lines = n_blob = 0
        for c in comps:
            tr = vx.trace_stroke(c["coords"], c["bbox"], (H, W))
            if tr is not None:
                elements.append(_stroke_to_line_element(tr, vx.component_color(La, c["coords"])))
                n_lines += 1
                continue
            x0, y0, x1, y1 = c["bbox"]
            sub = np.zeros((y1 - y0, x1 - x0), dtype=bool)
            sub[c["coords"][:, 0] - y0, c["coords"][:, 1] - x0] = True
            raw = vx.blob_trace(sub, (x0, y0), vx.component_color(La, c["coords"]))
            if raw is not None:
                elements.append(raw)
                n_blob += 1

        _sibling("figures")
        import figures_overlay as fov
        from figures_common import render_svg_to_png

        # 5. ARTWORK OBJECTS: split the (bg-removed) artwork into its connected pieces and place each
        # as its OWN embedded `image` element — so every object (apple/mango/banana, or the single
        # cross-section) is individually SELECTABLE in the SVG (and PPTX via the elements JSON).
        # Positions are the real pixels; nothing moves. (Vector mode instead vtraces the whole artwork.)
        vector_artwork = params.get("artwork_mode") == "vector"
        obj_els = []
        if not vector_artwork:
            for idx, o in enumerate(vx.split_objects(art_arr)):
                ox, oy, ow, oh = o["bbox"]
                op = image.with_name(f"{stem}_obj{idx}.png")
                Image.fromarray(o["rgba"]).save(op)
                obj_els.append(
                    {"kind": "image", "path": str(op), "x": ox, "y": oy, "width": ow, "height": oh}
                )
        elements = obj_els + elements  # artwork objects at the BOTTOM, labels/arrows on top
        n_objects = len(obj_els)

        if not elements:
            raise RuntimeError(
                "nothing to vectorize — no artwork objects, text or lines found. For a plain image "
                "with no labels, use trace_image."
            )

        # write the elements spec so export_pptx / render_editable_overlay can reuse it (elements_path)
        out_json = image.with_name(stem + "_elements.json")
        out_json.write_text(
            json.dumps({"width": W, "height": H, "elements": elements}, indent=1), encoding="utf-8"
        )

        # 6. Build the editable SVG. Object mode -> the image elements ARE the artwork, build directly.
        # Vector mode -> vtrace the whole artwork and compose the overlay over it.
        if vector_artwork:
            from compose_layers_tool import ComposeLayersTool
            from trace_image_tool import TraceImageTool

            overlay_svg = fov.build_svg(
                {"width": W, "height": H, "font_family": fov.DEFAULT_FONT, "elements": elements}
            )
            overlay_path.write_text(overlay_svg, encoding="utf-8")
            traced = image.with_name(stem + "_artwork.svg")
            TraceImageTool(self.config)._run(
                {"image": str(base_path), "out_svg": str(traced), "mode": "color"}
            )
            compose = ComposeLayersTool(self.config)
            art = {"artwork_svg_path": str(traced), "overlay_svg_path": str(overlay_path)}
            out_svg_final = compose._run({**art, "out_svg": str(out_svg)})["out_svg"]
            png = None
            try:
                png = compose._run({**art, "out_png": str(out_png)})["out_png"]
            except Exception:
                png = None
        else:
            svg = fov.build_svg(
                {"width": W, "height": H, "font_family": fov.DEFAULT_FONT, "elements": elements}
            )
            out_svg.write_text(svg, encoding="utf-8")
            out_svg_final = str(out_svg)
            png = None
            try:  # preview PNG needs a headless browser — best-effort, never loses the SVG
                render_svg_to_png(svg, out_png, W, H, background="#FFFFFF")
                png = str(out_png)
            except Exception:
                png = None

        return {
            "out_svg": out_svg_final,
            "out_png": png,
            "out_json": str(out_json),
            "base_png": str(base_path),
            "overlay_svg": str(overlay_path),
            "width": W,
            "height": H,
            "diff_fraction": round(frac, 4),
            "labels": len(label_boxes),
            "objects": n_objects,
            "lines": n_lines,
            "blob_shapes": n_blob,
            "cleaned_labels": n_cleaned,
            "strip_failed": strip_failed,
            "bg_removed": bg_removed,
            "artwork_mode": params.get("artwork_mode", "raster"),
            "stripped": stripped,
            "cached": False,
        }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"figure_to_svg failed: {e}", is_error=True)
        if r.get("cached"):
            return ToolResult.text(
                f"Already vectorised — showing the existing editable SVG -> {r['out_svg']} "
                f"(pass force:true to reconvert).",
                details=r,
                artifacts=[p for p in (r["out_svg"], r["out_png"]) if p],
            )
        made = f" (stripped a textless base -> {r['base_png']})" if r["stripped"] else ""
        deliverables = [p for p in (r["out_svg"], r["out_png"]) if p]
        preview = f" Preview -> {r['out_png']}." if r["out_png"] else ""
        warn = ""
        if r["strip_failed"]:
            warn = (
                " NOTE: the image model did NOT remove the labels/lines (it reproduced the figure), "
                "so the leader lines/arrows are kept AS ARTWORK (only the text is editable) to avoid "
                "doubling. For editable vector arrows too, retry (the strip is non-deterministic) or "
                "pass artwork_mode='vector' to trace the whole thing."
            )
        bg = " bg removed" if r.get("bg_removed") else ""
        objs = f"{r.get('objects', 0)} selectable object(s), " if r.get("objects") else ""
        content = [
            TextContent(
                text=(
                    f"Editable SVG -> {r['out_svg']} ({r['width']}x{r['height']}, {r['artwork_mode']} "
                    f"artwork{bg}): {objs}{r['labels']} label(s), {r['lines']} line(s), "
                    f"{r['blob_shapes']} traced shape(s){made}.{warn}{preview}"
                )
            )
        ]
        if r["out_png"]:
            try:
                _sibling("figures")
                from figures_common import png_block

                content.append(png_block(Path(r["out_png"])))
            except Exception:
                pass
        return ToolResult(content=content, details=r, artifacts=deliverables)
