"""extract_annotations: labelled figure -> its annotation layer as EDITABLE overlay elements.

The SEMANTIC vectorizer for figures WE generated (or any labelled raster + its textless base).
Instead of asking a VLM to read coordinates (read_labels_from_image — kept as the fallback route),
this tool gets the geometry from the pixels themselves:

  diff(L, T') -> annotation mask -> per-component:
    text   -> OCR (RapidOCR) -> editable `label` elements at the drawn position
    stroke -> skeleton centerline -> waypoints + stroke width + arrowhead test
              -> `arrow` / `leader` elements (curved routes + real heads preserved)
    other  -> optional vtracer binary trace -> `raw` vector paths (filled shapes)

Because T' is derived FROM L (auto-strip when no `base` is given), alignment is guaranteed and the
label/arrow positions are correct by construction — the model's own placement, kept. The mask
coverage fraction doubles as the alignment gate: a huge diff means the strip drifted (retry).

Deps (optional install, actionable error if absent): numpy + scikit-image (core), rapidocr (text),
vtracer (filled-shape fallback only). `pip install agentd[figures-vector]`.
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import vectorize_extract as vx

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.application.tool_models import resolve_tool_model

# Strip prompt — same wording the vision plugin uses (read_labels_from_image), duplicated here
# because plugins are sys.path-isolated siblings; keep the two in step if you edit one.
_STRIP_PROMPT = (
    "Reproduce this EXACT image, pixel-for-pixel, but REMOVE — completely and everywhere — every text "
    "label, word, number, callout, legend, title and caption; every leader line, pointer line, arrow and "
    "arrowhead; and every marker, dot, bracket, tick, measurement mark and scale bar. Also remove any "
    "background, backdrop, scenery or colour fill behind the subject. Keep ALL artwork, anatomy, colours "
    "and shading unchanged and sharp, on a pure solid white background. There must be NO text, NO leader "
    "or pointer lines, NO arrows and NO markers anywhere in the output — only the clean illustration on "
    "a pure white background. The final image should be a clean image which contains the artwork but no lines, markers, text or other pointers."
)

# Alignment gate thresholds on diff coverage (fraction of pixels changed L->T').
_ALIGN_WARN = 0.20
_ALIGN_FAIL = 0.45


def _quad_bbox(quad):
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return min(xs), min(ys), max(xs), max(ys)


def _text_color(La, box) -> str:
    """Ink colour of an OCR'd label, sampled from the labelled image: the median of the DARKER
    half of the box (the glyph pixels, not the background)."""
    import numpy as np

    x0, y0, x1, y1 = (int(v) for v in box)
    crop = La[max(0, y0) : max(1, y1), max(0, x0) : max(1, x1)].reshape(-1, 3)
    if crop.size == 0:
        return "#1f2937"
    lum = crop.mean(axis=1)
    dark = crop[lum < lum.mean()]
    px = dark if len(dark) else crop
    med = np.median(px, axis=0).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*(int(v) for v in med))


def _center_in_boxes(bbox, boxes, pad: float = 4.0) -> bool:
    """Is a component's centre inside any (padded) label box? Used to drop the diff'd TEXT pixels
    from stroke tracing (text is handled by OCR, not the skeletoniser)."""
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for lx0, ly0, lx1, ly1 in boxes:
        if lx0 - pad <= cx <= lx1 + pad and ly0 - pad <= cy <= ly1 + pad:
            return True
    return False


def _ocr_lines(png_path: Path):
    """Run RapidOCR on an image -> [{text, quad [(x,y)x4], score}]. Tolerates both the modern
    `rapidocr` (RapidOCROutput with .boxes/.txts/.scores) and the older tuple-list API."""
    try:
        from rapidocr import RapidOCR  # modern package (PP-OCRv5 via onnxruntime)
    except ImportError:
        try:
            from rapidocr_onnxruntime import RapidOCR  # legacy package name
        except ImportError as e:
            raise RuntimeError(
                "extract_annotations needs RapidOCR for the text layer "
                "(pip install 'agentd[figures-vector]' or: pip install rapidocr). "
                "Alternative without it: read_labels_from_image (VLM route)."
            ) from e
    engine = RapidOCR()
    res = engine(str(png_path))
    lines = []
    boxes = getattr(res, "boxes", None)
    if boxes is not None:  # modern output object
        txts = list(getattr(res, "txts", []) or [])
        scores = list(getattr(res, "scores", []) or [])
        for i, box in enumerate(list(boxes)):
            quad = [(float(p[0]), float(p[1])) for p in box]
            lines.append(
                {
                    "text": str(txts[i]) if i < len(txts) else "",
                    "quad": quad,
                    "score": float(scores[i]) if i < len(scores) else 1.0,
                }
            )
        return [ln for ln in lines if ln["text"].strip()]
    if isinstance(res, tuple):  # legacy: (list, elapse)
        res = res[0]
    for item in res or []:
        box, text, score = item[0], item[1], item[2] if len(item) > 2 else 1.0
        quad = [(float(p[0]), float(p[1])) for p in box]
        if str(text).strip():
            lines.append({"text": str(text), "quad": quad, "score": float(score)})
    return lines


def strip_verify(labelled: Path, stripped: Path, thr: float = 0.45):
    """VERIFY a strip actually removed the labels. The image model often just ECHOES the input
    (reproduces the figure with the labels intact) — so OCR the labelled image AND the stripped
    output and compare: if the stripped image STILL holds most of the original's text, the strip
    FAILED. Returns (removed_ok, n_text_before, n_text_after). OCR-unavailable => (True, 0, 0) so it
    never blocks the pipeline."""
    try:
        before = _ocr_lines(labelled)
        after = _ocr_lines(stripped)
    except Exception:
        return True, 0, 0
    if len(before) < 2:
        return True, len(before), len(after)  # ~nothing to remove
    return (len(after) / len(before) <= thr), len(before), len(after)


def strip_labels(config, labelled: Path, api_key, verify: bool = True) -> Path:
    """Strip the labels off a labelled figure via the image model -> a clean textless base (aligned
    to the input). VERIFICATION + RETRY loop: after each attempt it checks (via OCR) that the labels
    are actually gone; if the model just echoed the input, it retries — escalating through the strip
    model list (config plugins.figure-art.tools.generate_artwork.strip_models, else one retry of the
    primary) — before giving up. Whether it ultimately succeeded is checkable by the caller (and by
    figure_to_svg's own strip-failure detection) so the agent can be told. Shared by both tools.

    The generation goes through the runtime's model funnel (`oneshot.generate_image`) — imported at
    call time so the plugin sandbox can serve it host-side — so it is mode-correct (BYOK direct /
    cloud proxied+metered) and this module holds no credential."""
    from agent_runtime.application.tool_models import tool_config
    from agent_runtime.infrastructure.llm.oneshot import generate_image

    primary = resolve_tool_model(
        config, "figure-art", "generate_artwork", default=None, kind="image-gen"
    )
    extra = (
        tool_config(config, "figure-art", "generate_artwork", "strip_models", default=None) or []
    )
    attempts = [primary] + [m for m in extra if m and m != primary]
    if len(attempts) == 1:
        attempts.append(primary)  # nothing to escalate to -> retry once (catches a transient echo)

    out = labelled.with_name(labelled.stem + "_textless.png")
    last_exc = None
    for model in attempts:
        try:
            generate_image(
                model=model, prompt=_STRIP_PROMPT, out_path=out, api_key=api_key,
                reference_images=[labelled],
            )
        except Exception as e:  # noqa: BLE001 — try the next model/attempt before failing
            last_exc = e
            continue
        if not verify:
            return out
        ok, _, _ = strip_verify(labelled, out)
        if ok:
            return out  # labels gone — done
    if not out.is_file() and last_exc is not None:
        raise last_exc  # every attempt errored and produced nothing
    return out  # exhausted attempts (strip may have echoed) — caller detects + tells the agent


class ExtractAnnotationsTool(Tool):
    name = "extract_annotations"
    plugin = "vectorize"
    description = (
        "SEMANTIC vectorizer for a labelled figure: pixel-diff the LABELLED image against its "
        "TEXTLESS base -> every drawn annotation, as ready-to-draw render_editable_overlay elements. "
        "Text -> OCR'd editable `label`s at the exact drawn position; leader lines & arrows -> "
        "`leader`/`arrow` elements with the model's own geometry (curved routes, stroke width, "
        "colour, detected arrowheads); other filled marks -> `raw` traced paths. Pass `base`=the "
        "textless art if you have it; omit it and the tool STRIPS the labels off `labeled` via the "
        "image model to make one (returned as `base_png` — alignment then guaranteed). Writes the "
        "full spec to `out_json`; feed that to render_editable_overlay via `elements_path` (don't "
        "retype elements), then compose_figure_layers over `base_png`. Positions come from pixels, "
        "not a VLM, so placement is exact. Fallback when this reports unanchored labels or the "
        "diff looks wrong: read_labels_from_image. Needs numpy+scikit-image+rapidocr "
        "(pip install 'agentd[figures-vector]')."
    )
    label = "Extract Annotations"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["labeled"],
        "properties": {
            "labeled": {
                "type": "string",
                "description": "Path to the LABELLED image (labels/leaders/arrows drawn on it).",
            },
            "base": {
                "type": "string",
                "description": "Optional path to the pixel-aligned TEXTLESS base. OMIT to auto-strip the labels off `labeled` via the image model (recommended — alignment guaranteed).",
            },
            "out_json": {
                "type": "string",
                "description": "Where to write the overlay spec JSON {width,height,elements}. Default: <labeled>_elements.json next to the image.",
            },
            "diff_threshold": {
                "type": "integer",
                "description": "Per-channel diff threshold 0-255 for the annotation mask. Default 40; lower catches fainter strokes.",
            },
            "api_key": {
                "type": "string",
                "description": "Optional BYOK key override for the auto-strip (else GEMINI_API_KEY/GOOGLE_API_KEY). Ignored in cloud mode.",
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
        return strip_labels(self.config, labelled, api_key)

    # ---------------------------------------------------------------- core
    def _run(self, params: dict) -> dict:
        vx._require_deps()
        import numpy as np
        from PIL import Image

        labeled_path = self._resolve(params["labeled"])
        stripped = False
        if params.get("base"):
            base_path = self._resolve(params["base"])
        else:
            base_path = self._strip_labels(labeled_path, params.get("api_key"))
            stripped = True

        L = Image.open(labeled_path).convert("RGB")
        B = Image.open(base_path).convert("RGB")
        if B.size != L.size:  # strip/base drift in size: bring the base into L's space
            B = B.resize(L.size, Image.LANCZOS)
        W, H = L.size
        La, Ba = np.asarray(L), np.asarray(B)

        mask, frac = vx.diff_mask(La, Ba, thr=int(params.get("diff_threshold", 40)))
        if frac > _ALIGN_FAIL:
            raise RuntimeError(
                f"alignment gate: {frac:.0%} of pixels differ between the labelled image and the "
                f"base — the textless base is a DIFFERENT picture (strip drifted or wrong base). "
                f"Re-strip (call again without `base`) or pass the correct pixel-aligned base."
            )

        # the overlay engine auto-scales style sizes by s; we measured REAL pixels, so pre-divide
        s = max(1.0, max(W, H) / 1024.0)

        # ---- OCR the LABELLED image DIRECTLY ------------------------------------------------
        # Text is read from L itself, NOT from the diff. This is the key robustness fix: an
        # imperfect strip that leaves labels in the base can no longer hide them from OCR (the old
        # diff-only OCR saw only the labels the strip happened to remove).
        lines = _ocr_lines(labeled_path)
        # Drop implausible detections: OCR sometimes reads a piece of ARTWORK as text (a round
        # organelle -> "O"). A real label is a short line, not a tall/huge block — filter by box
        # size and confidence so we neither emit garbage labels nor clean over artwork.
        kept = []
        for ln in lines:
            bx0, by0, bx1, by1 = _quad_bbox(ln["quad"])
            if by1 - by0 > 0.14 * H or bx1 - bx0 > 0.9 * W:
                continue  # too tall/wide to be a text label — it's artwork
            if float(ln.get("score", 1.0)) < 0.4 or not ln["text"].strip():
                continue
            ln["_box"] = (bx0, by0, bx1, by1)
            kept.append(ln)
        lines = kept
        label_boxes = [ln["_box"] for ln in lines]

        # ---- CLEAN base: erase baked labels so they can't double under the editable <text> ----
        # (shared with figure_to_svg: margins vanish invisibly, textured artwork is left as a faint
        # ghost rather than patched — see vectorize_extract.clean_base.)
        base_clean, strip_left = vx.clean_base(La, Ba, label_boxes)
        base_out = base_path
        if strip_left:
            base_out = labeled_path.with_name(labeled_path.stem + "_base_clean.png")
            Image.fromarray(base_clean).save(base_out)

        elements: list[dict] = []

        # ---- text -> editable labels at the drawn position (colour sampled from L) ----
        for ln in lines:
            x0, y0, x1, y1 = ln["_box"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            fs = min(max((y1 - y0) * 0.9, 8.0), 44.0)
            elements.append(
                {
                    "kind": "label",
                    "text": ln["text"],
                    "x": round(cx, 1),
                    "y": round(cy + fs * 0.32, 1),  # baseline from centre
                    "anchor": "middle",
                    "font_size": round(fs / s, 1),
                    "color": _text_color(La, ln["_box"]),
                    "weight": "500",
                }
            )

        # ---- strokes from the diff -> leaders / arrows, FILTERED to REAL annotations -------
        # A genuine leader touches a label; a genuine arrow has a head. Any other diff blob on a
        # strip that redrew the artwork is redraw NOISE (this is what produced dozens of bogus
        # "leaders" before). So: keep a stroke only if it has an arrowhead OR an endpoint near a
        # label box; drop the rest.
        reach = 0.05 * max(W, H) + 25

        def _near_label(pts) -> bool:
            for ex, ey in pts:
                for lx0, ly0, lx1, ly1 in label_boxes:
                    cx = min(max(ex, lx0), lx1)
                    cy = min(max(ey, ly0), ly1)
                    if math.hypot(ex - cx, ey - cy) <= reach:
                        return True
            return False

        comps = vx.components(mask)
        stroke_ends: list[tuple[float, float]] = []
        n_arrows = n_leaders = n_raw = n_noise = 0
        for c in comps:
            if _center_in_boxes(c["bbox"], label_boxes):  # the diff'd text itself — OCR handled it
                continue
            tr = vx.trace_stroke(c["coords"], c["bbox"], (H, W))
            if tr is None:
                # a filled, non-stroke blob: keep as a traced shape ONLY if near a label
                # (a detached arrowhead / marker); otherwise it's redraw noise.
                bx0, by0, bx1, by1 = c["bbox"]
                if not _near_label([(bx0, by0), (bx1, by1), ((bx0 + bx1) / 2, (by0 + by1) / 2)]):
                    n_noise += 1
                    continue
                sub = np.zeros((by1 - by0, bx1 - bx0), dtype=bool)
                sub[c["coords"][:, 0] - by0, c["coords"][:, 1] - bx0] = True
                raw = vx.blob_trace(sub, (bx0, by0), vx.component_color(La, c["coords"]))
                if raw is not None:
                    elements.append(raw)
                    n_raw += 1
                else:
                    n_noise += 1
                continue
            pts, route = vx.simplify_waypoints(tr["points"], tr["width"])
            head_start, head_end = tr["head_start"], tr["head_end"]
            if not (head_start or head_end or _near_label(pts)):
                n_noise += 1  # redraw noise: no head, touches no label
                continue
            # head belongs at points[-1] (marker-end); flip when only the start widened
            if head_start and not head_end:
                pts = list(reversed(pts))
                head_start, head_end = False, True
            color = vx.component_color(La, c["coords"])
            stroke_ends.extend([pts[0], pts[-1]])
            if head_end:
                el = {
                    "kind": "arrow",
                    "points": [list(p) for p in pts],
                    "route": route,
                    "color": color,
                    "body": "stroked",
                    "width": round(max(tr["width"], 1.5) / s, 2),
                    "head": "standard",
                    "head_size": round(min(max(tr["width"] * 3.2, 8.0), 24.0) / s, 1),
                }
                if head_start:
                    el["start_head"] = "standard"
                elements.append(el)
                n_arrows += 1
            else:
                elements.append(
                    {
                        "kind": "leader",
                        "points": [list(p) for p in pts],
                        "route": route,
                        "color": color,
                        "width": round(max(tr["width"], 1.0) / s, 2),
                        "dot": False,
                    }
                )
                n_leaders += 1

        # ---- leader-presence gate: which labels ended up with no stroke nearby ----
        unanchored = []
        for ln in lines:
            x0, y0, x1, y1 = ln["_box"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if not any(math.hypot(ex - cx, ey - cy) <= reach for ex, ey in stroke_ends):
                unanchored.append(ln["text"])

        spec = {"width": W, "height": H, "elements": elements}
        out_json = self._resolve(
            params.get("out_json")
            or str(labeled_path.with_name(labeled_path.stem + "_elements.json"))
        )
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(spec, indent=1), encoding="utf-8")

        return {
            "out_json": str(out_json),
            "base_png": str(base_out),
            "stripped": stripped,
            "strip_left": strip_left,
            "width": W,
            "height": H,
            "diff_fraction": round(frac, 4),
            "labels": len(lines),
            "arrows": n_arrows,
            "leaders": n_leaders,
            "raw_blobs": n_raw,
            "noise_dropped": n_noise,
            "unanchored": unanchored,
            "elements": elements,
        }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"extract_annotations failed: {e}", is_error=True)
        made = f" Stripped a textless base -> {r['base_png']}." if r["stripped"] else ""
        warn = ""
        if r["strip_left"]:
            warn += (
                f" NOTE: the strip left {r['strip_left']} label(s) in the base; blended those boxes "
                f"into the local background in {r['base_png']} so text won't double. If the strip "
                f"barely changed the image, leader/arrow tracing is limited (labels are still "
                f"editable) — for full arrows, re-strip (call again without `base`) or use "
                f"read_labels_from_image."
            )
        elif r["diff_fraction"] > _ALIGN_WARN:
            warn += (
                f" WARNING: {r['diff_fraction']:.0%} of pixels differ — the strip may have altered "
                f"artwork; LOOK at {r['base_png']} before composing."
            )
        if r["unanchored"]:
            warn += (
                f" {len(r['unanchored'])} label(s) have no leader/arrow nearby "
                f"({', '.join(t for t in r['unanchored'][:5])}…): drawn unanchored — add leaders via "
                f"read_labels_from_image for those, or accept them as floating text."
            )
        if r["labels"] == 0:
            return ToolResult.text(
                f"extract_annotations read NO text from {r['width']}x{r['height']} — OCR found no "
                f"labels on the image. If it IS labelled, the text may be too small/low-contrast "
                f"(try a 2K generation) — or use read_labels_from_image (VLM route).{made}",
                details=r,
                is_error=True,
            )
        return ToolResult.text(
            f"Extracted {r['labels']} label(s), {r['arrows']} arrow(s), {r['leaders']} leader(s)"
            + (f", {r['raw_blobs']} traced shape(s)" if r["raw_blobs"] else "")
            + (f", {r['noise_dropped']} noise blob(s) dropped" if r["noise_dropped"] else "")
            + f" from {r['width']}x{r['height']} (diff {r['diff_fraction']:.1%}).{made}{warn} "
            f'Spec -> {r["out_json"]}. Next: render_editable_overlay(elements_path="{r["out_json"]}") '
            f'then compose_figure_layers(artwork="{r["base_png"]}", overlay_svg_path=…).',
            details=r,
        )
