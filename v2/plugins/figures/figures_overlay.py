"""overlay.py — the deterministic SVG annotation engine (pure stdlib, no agentd imports).

This is the fidelity core. The agent NEVER hand-writes SVG path coordinates; it produces a
high-level element spec (labels, leaders, arrows with a *style name*) and this module turns that
into pixel-perfect, editable SVG: real <text>, real <path> arrows, gradients, custom markers and
filters. That separation is exactly why the overlay beats baked-in (diffusion) labels/arrows —
crisp at any zoom, correct spelling, recolourable, never smudged.

Coordinate system = the artwork's pixel space (origin top-left). So an arrow's points are the same
numbers a vision model reports when it locates structures on the generated image.

Public API:
    build_svg(spec)   -> full standalone <svg> string
    build_inner(spec) -> (defs_str, body_str, width, height)   # for nesting / layering

`spec` = {
    "width": int, "height": int,
    "background": str|None,                 # CSS colour for an opaque overlay; default transparent
    "font_family": str,                     # default a clean sans stack
    "elements": [ <element>, ... ],         # drawn in order (later = on top)
}

Element kinds (each a dict with "kind"):
    arrow      — a connector: straight | elbow | curved, stroked | tapered, optional gradient,
                 soft|standard|none head, glow|shadow filter, dashed.
    leader     — a thin annotation line (label -> structure), optional target dot, elbow/curved.
    label      — a text label, optionally inside a rounded "pill"/box (the BioRender callout look).
    annotation — convenience = boxed label + leader + target dot in one (the membrane-callout).
    panel      — a rounded rectangle region/group frame (the "Re-infection" boundary box).
    dot        — a small filled marker at a point.
    raw        — escape hatch: literal SVG markup passed straight through.
"""

from __future__ import annotations

import base64
import math
from pathlib import Path
from xml.sax.saxutils import escape

DEFAULT_FONT = "Inter, 'Helvetica Neue', Arial, 'Segoe UI', sans-serif"

_IMG_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


# ===========================================================================
# small numeric helpers
# ===========================================================================
def _f(x: float) -> str:
    """Compact fixed-point — keeps the SVG small and diffs readable."""
    return f"{x:.2f}".rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


def _pt(p) -> str:
    return f"{_f(p[0])},{_f(p[1])}"


def _lerp(a, b, t):
    return a + (b - a) * t


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _approx_text_w(text: str, font_size: float, weight: str) -> float:
    """Cheap text-width estimate (no font metrics available headless). Slightly generous so
    auto-sized label boxes never clip. ~0.52em average glyph advance, a touch wider when bold."""
    k = 0.56 if str(weight) in ("600", "700", "bold") else 0.52
    return len(text) * font_size * k


# ===========================================================================
# centerline construction: polyline, rounded polyline, smooth spline
# ===========================================================================
def _catmull_rom_to_bezier(pts):
    """Turn a list of >=2 points into a smooth cubic path `d` via Catmull-Rom -> Bezier.
    Used for "curved" connectors through arbitrary waypoints (e.g. the looping re-infection
    arrows). Endpoints are duplicated so the curve actually touches the first/last point."""
    if len(pts) == 2:
        # one segment: gentle bow using a perpendicular offset at the midpoint
        return _bowed_cubic_d(pts[0], pts[1], bow=0.18)
    P = [pts[0]] + list(pts) + [pts[-1]]
    d = [f"M {_pt(pts[0])}"]
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        d.append(f"C {_pt(c1)} {_pt(c2)} {_pt(p2)}")
    return " ".join(d)


def _bowed_cubic_d(a, b, bow=0.18):
    """A single cubic from a->b bowed sideways by `bow` * length (perpendicular)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / L, dx / L
    off = bow * L
    c1 = (a[0] + dx / 3 + nx * off, a[1] + dy / 3 + ny * off)
    c2 = (a[0] + 2 * dx / 3 + nx * off, a[1] + 2 * dy / 3 + ny * off)
    return f"M {_pt(a)} C {_pt(c1)} {_pt(c2)} {_pt(b)}"


def _rounded_polyline_d(pts, radius):
    """Polyline with rounded corners (quadratic fillets) — the clean orthogonal "elbow" look
    that ELK/dagre routing wants. radius is clamped per-corner so short segments don't overshoot."""
    if len(pts) <= 2 or radius <= 0:
        return "M " + " L ".join(_pt(p) for p in pts)
    d = [f"M {_pt(pts[0])}"]
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        r = min(radius, _dist(p0, p1) / 2, _dist(p1, p2) / 2)
        v0 = ((p0[0] - p1[0]), (p0[1] - p1[1]))
        v2 = ((p2[0] - p1[0]), (p2[1] - p1[1]))
        l0 = math.hypot(*v0) or 1.0
        l2 = math.hypot(*v2) or 1.0
        a = (p1[0] + v0[0] / l0 * r, p1[1] + v0[1] / l0 * r)
        b = (p1[0] + v2[0] / l2 * r, p1[1] + v2[1] / l2 * r)
        d.append(f"L {_pt(a)} Q {_pt(p1)} {_pt(b)}")
    d.append(f"L {_pt(pts[-1])}")
    return " ".join(d)


def _centerline_d(pts, route, corner_radius):
    """Dispatch a connector's centerline `d` by route style."""
    if route == "curved":
        return _catmull_rom_to_bezier(pts)
    if route == "elbow":
        return _rounded_polyline_d(pts, corner_radius)
    return "M " + " L ".join(_pt(p) for p in pts)  # straight


# ===========================================================================
# tapered (filled) arrow body — the "premium" look
# ===========================================================================
def _densify(pts, route, n=48):
    """Sample a connector's centerline into ~n points with their tangents, so a filled body can
    follow even a curved/elbow path. Returns [(x, y, tx, ty), ...] (t = unit tangent)."""
    # Build a flat polyline approximation of the centerline, then resample by arc length.
    if route == "curved":
        flat = _flatten_catmull(pts, samples_per_seg=18)
    elif route == "elbow":
        flat = pts
    else:
        flat = pts
    # cumulative length
    seg = [0.0]
    for i in range(1, len(flat)):
        seg.append(seg[-1] + _dist(flat[i - 1], flat[i]))
    total = seg[-1] or 1.0
    out = []
    for i in range(n + 1):
        target = total * i / n
        # find segment
        j = 1
        while j < len(seg) and seg[j] < target:
            j += 1
        j = min(j, len(flat) - 1)
        t = 0.0 if seg[j] == seg[j - 1] else (target - seg[j - 1]) / (seg[j] - seg[j - 1])
        x = _lerp(flat[j - 1][0], flat[j][0], t)
        y = _lerp(flat[j - 1][1], flat[j][1], t)
        dx = flat[j][0] - flat[j - 1][0]
        dy = flat[j][1] - flat[j - 1][1]
        L = math.hypot(dx, dy) or 1.0
        out.append((x, y, dx / L, dy / L))
    return out


def _flatten_catmull(pts, samples_per_seg=18):
    """Evaluate the Catmull-Rom spline numerically into a dense polyline (for arc-length work)."""
    if len(pts) == 2:
        # match _bowed_cubic_d so taper follows the same bow
        a, b = pts
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L
        off = 0.18 * L
        c1 = (a[0] + dx / 3 + nx * off, a[1] + dy / 3 + ny * off)
        c2 = (a[0] + 2 * dx / 3 + nx * off, a[1] + 2 * dy / 3 + ny * off)
        return [_cubic(a, c1, c2, b, i / samples_per_seg) for i in range(samples_per_seg + 1)]
    P = [pts[0]] + list(pts) + [pts[-1]]
    flat = [pts[0]]
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        for s in range(1, samples_per_seg + 1):
            flat.append(_cubic(p1, c1, c2, p2, s / samples_per_seg))
    return flat


def _cubic(p0, p1, p2, p3, t):
    mt = 1 - t
    a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
    return (
        a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
        a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
    )


def tapered_arrow_d(pts, route, w_tail, w_head, head_len, head_w):
    """A CLOSED filled path that tapers from w_tail (tail) to w_head (where the head begins), then
    a triangular head of width head_w to the tip. The secret to the soft, weighty arrows: it's a
    *fill*, not a stroke, so the width can vary along the length. Works for straight/elbow/curved."""
    samples = _densify(pts, route, n=56)
    tip = samples[-1][:2]
    # find the point where the head begins (head_len back from the tip along the path)
    # walk backwards accumulating length
    acc, base_i = 0.0, len(samples) - 1
    for i in range(len(samples) - 1, 0, -1):
        acc += _dist(samples[i][:2], samples[i - 1][:2])
        if acc >= head_len:
            base_i = i - 1
            break
    base = samples[base_i]
    bx, by, btx, bty = base
    bnx, bny = -bty, btx
    body = samples[: base_i + 1] or [samples[0]]
    total_n = len(body) - 1 or 1
    top, bot = [], []
    for i, (x, y, tx, ty) in enumerate(body):
        t = i / total_n
        hw = _lerp(w_tail, w_head, t) / 2.0
        nx, ny = -ty, tx
        top.append((x + nx * hw, y + ny * hw))
        bot.append((x - nx * hw, y - ny * hw))
    head_l = (bx + bnx * head_w / 2, by + bny * head_w / 2)
    head_r = (bx - bnx * head_w / 2, by - bny * head_w / 2)
    d = [f"M {_pt(top[0])}"]
    d += [f"L {_pt(p)}" for p in top[1:]]
    d += [f"L {_pt(head_l)}", f"L {_pt(tip)}", f"L {_pt(head_r)}"]
    d += [f"L {_pt(p)}" for p in reversed(bot)]
    d.append("Z")
    return " ".join(d)


# ===========================================================================
# defs registry (gradients / markers / filters), de-duplicated by id
# ===========================================================================
class _Defs:
    def __init__(self):
        self.items = {}

    def add(self, key, markup):
        self.items.setdefault(key, markup)
        return key

    def gradient(self, c0, c1, p0, p1):
        key = f"grad_{abs(hash((c0, c1, round(p0[0]), round(p0[1]), round(p1[0]), round(p1[1])))) & 0xFFFFFF:06x}"
        self.add(
            key,
            (
                f'<linearGradient id="{key}" gradientUnits="userSpaceOnUse" '
                f'x1="{_f(p0[0])}" y1="{_f(p0[1])}" x2="{_f(p1[0])}" y2="{_f(p1[1])}">'
                f'<stop offset="0" stop-color="{escape(c0)}"/>'
                f'<stop offset="1" stop-color="{escape(c1)}"/></linearGradient>'
            ),
        )
        return key

    def marker(self, color, kind, size, reverse=False):
        """A reusable arrowhead. `kind`: triangle | soft | bar | circle | diamond. Uses
        orient="auto" (SVG 1.1 — rendered by every editor, unlike SVG2 auto-start-reverse). For a
        start-of-path head (`reverse`), the shape is mirrored so it points outward with plain auto."""
        kind = kind if kind in ("triangle", "soft", "bar", "circle", "diamond") else "triangle"
        c = escape(color)
        key = f"mk_{kind}{int(size)}{'r' if reverse else ''}_{color.lstrip('#')}"
        if kind == "soft":  # gentle curved BioRender head
            w, h = size, size * 0.85
            path = f'<path d="M0,0 C{_f(w * 0.45)},{_f(h * 0.22)} {_f(w * 0.45)},{_f(h * 0.78)} 0,{_f(h)} L{_f(w)},{_f(h / 2)} Z" fill="{c}"/>'
            refX, refY, mw, mh = w * 0.92, h / 2, w, h
        elif kind == "bar":  # blunt perpendicular bar — the inhibition symbol ⊣
            w, h = size * 0.55, size * 1.35
            x0, x1 = w * 0.34, w * 0.62
            path = f'<path d="M{_f(x0)},0 L{_f(x1)},0 L{_f(x1)},{_f(h)} L{_f(x0)},{_f(h)} Z" fill="{c}"/>'
            refX, refY, mw, mh = (x0 + x1) / 2, h / 2, w, h
        elif kind == "circle":  # round cap / node marker
            d = size * 0.85
            path = f'<circle cx="{_f(d / 2)}" cy="{_f(d / 2)}" r="{_f(d / 2)}" fill="{c}"/>'
            refX, refY, mw, mh = d / 2, d / 2, d, d
        elif kind == "diamond":
            w, h = size, size * 0.9
            path = f'<path d="M0,{_f(h / 2)} L{_f(w / 2)},0 L{_f(w)},{_f(h / 2)} L{_f(w / 2)},{_f(h)} Z" fill="{c}"/>'
            refX, refY, mw, mh = w * 0.9, h / 2, w, h
        else:  # crisp triangle
            w, h = size, size * 0.85
            path = f'<path d="M0,0 L{_f(w)},{_f(h / 2)} L0,{_f(h)} Z" fill="{c}"/>'
            refX, refY, mw, mh = w * 0.92, h / 2, w, h
        if reverse:  # mirror so a start-head points outward under orient="auto"
            path = f'<g transform="translate({_f(mw)},0) scale(-1,1)">{path}</g>'
            refX = mw - refX
        self.add(
            key,
            (
                f'<marker id="{key}" markerWidth="{_f(mw)}" markerHeight="{_f(mh)}" '
                f'refX="{_f(refX)}" refY="{_f(refY)}" orient="auto" markerUnits="userSpaceOnUse">'
                f"{path}</marker>"
            ),
        )
        return key

    def glow(self, std=2.5):
        key = f"glow{int(std * 10)}"
        self.add(
            key,
            (
                f'<filter id="{key}" x="-60%" y="-60%" width="220%" height="220%">'
                f'<feGaussianBlur stdDeviation="{_f(std)}" result="b"/>'
                f'<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
            ),
        )
        return key

    def shadow(self):
        key = "softshadow"
        self.add(
            key,
            (
                f'<filter id="{key}" x="-30%" y="-30%" width="160%" height="160%">'
                f'<feDropShadow dx="0" dy="1.2" stdDeviation="1.6" flood-color="#000" flood-opacity="0.25"/>'
                f"</filter>"
            ),
        )
        return key

    def render(self):
        if not self.items:
            return ""
        return "<defs>" + "".join(self.items.values()) + "</defs>"


# ===========================================================================
# element renderers
# ===========================================================================
def _points_of(el):
    """Resolve an element's path points from either `points` or `from`/`to` (+ optional `via`)."""
    if el.get("points"):
        return [tuple(p) for p in el["points"]]
    pts = [tuple(el["from"])]
    pts += [tuple(p) for p in el.get("via", [])]
    pts.append(tuple(el["to"]))
    return pts


# Arrow style presets — a `style` name expands to a bundle of look defaults that explicit element
# fields still override. Two groups: generic LOOKS and semantic SCIENTIFIC arrows (pathway grammar).
# Default "clean" (soft head, weighty stroke) so an unstyled arrow never looks like a thin plain line.
# Every preset is still a fully editable vector path.
_ARROW_STYLES = {
    # --- generic looks ---
    "plain": {"body": "stroked", "head": "standard", "width": 3.0, "head_size": 12},
    "clean": {"body": "stroked", "head": "soft", "width": 4.0, "head_size": 16},
    "biorender": {
        "body": "tapered",
        "head": "soft",
        "w_tail": 12.0,
        "w_head": 3.5,
        "head_size": 18,
    },
    "subtle": {"body": "stroked", "head": "soft", "width": 2.5, "head_size": 12},
    # --- semantic scientific arrows (the pathway grammar) ---
    "flow": {"body": "stroked", "head": "soft", "width": 4.0, "head_size": 16},
    "activation": {"body": "stroked", "head": "standard", "width": 3.5, "head_size": 14},
    "inhibition": {"body": "stroked", "head": "bar", "width": 3.5, "head_size": 15},  # ⊣ blunt bar
    "reversible": {
        "body": "stroked",
        "head": "standard",
        "start_head": "standard",
        "width": 3.0,
        "head_size": 12,
    },  # ↔ double head
    "transport": {"body": "stroked", "head": "soft", "width": 3.0, "head_size": 14, "dash": "2 7"},
    "catalysis": {
        "body": "stroked",
        "head": "circle",
        "width": 2.5,
        "head_size": 12,
        "dash": "1 5",
    },
    "emphasis": {"body": "tapered", "head": "soft", "w_tail": 14.0, "w_head": 4.0, "head_size": 20},
}
DEFAULT_ARROW_STYLE = "clean"

# arrow/leader `head` names -> marker kind. "standard" == a crisp triangle; "none" draws nothing.
_HEAD_KIND = {
    "standard": "triangle",
    "triangle": "triangle",
    "soft": "soft",
    "bar": "bar",
    "circle": "circle",
    "diamond": "diamond",
}


def _head_marker(pos, head, color, size, defs):
    """Return a ` marker-end=...`/` marker-start=...` attribute for a head name (or '' for none).
    A start head is mirrored (reverse) so it points outward with the compatible orient="auto"."""
    kind = _HEAD_KIND.get(head)
    if not kind:
        return ""
    mid = defs.marker(color, kind, size, reverse=(pos == "start"))
    return f' marker-{pos}="url(#{mid})"'


def _arrow(el, defs, s=1.0):
    pts = _points_of(el)
    preset = _ARROW_STYLES.get(
        el.get("style", DEFAULT_ARROW_STYLE), _ARROW_STYLES[DEFAULT_ARROW_STYLE]
    )

    def g(key, default):
        return el.get(key, preset.get(key, default))

    route = el.get("route", "straight")
    color = el.get("color", "#374151")
    head = g("head", "standard")  # noqa: F841  # TODO: head style is read but never used (soft/none unimplemented)
    head_size = float(g("head_size", 12)) * s
    opacity = el.get("opacity", 1.0)
    filt = el.get("filter")  # None | glow | shadow
    cr = float(el.get("corner_radius", 10)) * s
    grad = el.get("gradient")  # {"from": c0, "to": c1}
    stroke_ref = color
    if grad:
        gid = defs.gradient(grad.get("from", color), grad.get("to", color), pts[0], pts[-1])
        stroke_ref = f"url(#{gid})"
    attrs = []
    if filt == "glow":
        attrs.append(f'filter="url(#{defs.glow()})"')
    elif filt == "shadow":
        attrs.append(f'filter="url(#{defs.shadow()})"')
    if opacity != 1.0:
        attrs.append(f'opacity="{_f(float(opacity))}"')
    extra = (" " + " ".join(attrs)) if attrs else ""

    if g("body", "stroked") == "tapered":
        d = tapered_arrow_d(
            pts,
            route,
            w_tail=float(g("w_tail", 13)) * s,
            w_head=float(g("w_head", 3.5)) * s,
            head_len=float(el["head_len"]) * s if "head_len" in el else head_size * 1.4,
            head_w=float(el["head_w"]) * s if "head_w" in el else head_size * 1.7,
        )
        return f'<path d="{d}" fill="{stroke_ref}"{extra}/>'

    # stroked body (optionally with marker heads at the end and/or the start)
    d = _centerline_d(pts, route, cr)
    sw = float(g("width", 3)) * s
    dash_v = g("dash", None)
    dash = f' stroke-dasharray="{dash_v}"' if dash_v else ""
    marker = _head_marker("end", g("head", "standard"), color, head_size, defs)
    marker += _head_marker("start", g("start_head", "none"), color, head_size, defs)
    return (
        f'<path d="{d}" fill="none" stroke="{stroke_ref}" stroke-width="{_f(sw)}" '
        f'stroke-linecap="round" stroke-linejoin="round"{dash}{marker}{extra}/>'
    )


def _leader(el, defs, s=1.0):
    pts = _points_of(el)
    color = el.get("color", "#6b7280")
    sw = float(el.get("width", 1.2)) * s
    dash = f' stroke-dasharray="{el.get("dash", "")}"' if el.get("dash") else ""
    head = el.get("head", "none")  # none | standard | soft | bar | circle — head at target end
    marker = _head_marker("end", head, color, float(el.get("head_size", 9)) * s, defs)
    d = _centerline_d(pts, el.get("route", "elbow"), float(el.get("corner_radius", 6)) * s)
    out = (
        f'<path d="{d}" fill="none" stroke="{escape(color)}" stroke-width="{_f(sw)}" '
        f'stroke-linecap="round" stroke-linejoin="round"{dash}{marker}/>'
    )
    # a target dot OR an arrowhead, not both (the head already marks the endpoint)
    if el.get("dot", head == "none"):
        r = float(el.get("dot_r", 2.6)) * s
        out += f'<circle cx="{_f(pts[-1][0])}" cy="{_f(pts[-1][1])}" r="{_f(r)}" fill="{escape(el.get("dot_color", color))}"/>'
    return out


def _label(el, defs, font_family, s=1.0):
    text = str(el.get("text", ""))
    x, y = float(el["x"]), float(el["y"])
    fs = float(el.get("font_size", 14)) * s
    anchor = el.get("anchor", "start")  # start | middle | end
    color = el.get("color", "#1f2937")
    weight = str(el.get("weight", "600"))
    ff = el.get("font_family", font_family)
    box = el.get("box")
    out = []
    tx = x
    if box:
        pad_x = float(box.get("pad_x", 9)) * s
        pad_y = float(box.get("pad_y", 5)) * s
        tw = _approx_text_w(text, fs, weight)
        bw = tw + 2 * pad_x
        bh = fs + 2 * pad_y
        if anchor == "middle":
            bx = x - bw / 2
        elif anchor == "end":
            bx = x - bw
        else:
            bx = x
        by = y - bh / 2
        rad = float(box.get("radius", 6)) * s
        fill = box.get("fill", "#ffffff")
        stroke = box.get("stroke", "#d1d5db")
        sw = float(box.get("stroke_width", 1)) * s
        filt = f' filter="url(#{defs.shadow()})"' if box.get("shadow") else ""
        out.append(
            f'<rect x="{_f(bx)}" y="{_f(by)}" width="{_f(bw)}" height="{_f(bh)}" '
            f'rx="{_f(rad)}" ry="{_f(rad)}" fill="{escape(fill)}" stroke="{escape(stroke)}" '
            f'stroke-width="{_f(sw)}"{filt}/>'
        )
        tx = bx + bw / 2
        anchor = "middle"
        y_text = by + bh / 2
    else:
        y_text = y
    dy = fs * 0.35  # vertical centering for dominant-baseline-less renderers
    out.append(
        f'<text x="{_f(tx if box else x)}" y="{_f(y_text + dy)}" '
        f'font-family="{ff}" font-size="{_f(fs)}" font-weight="{weight}" '
        f'fill="{escape(color)}" text-anchor="{anchor}">{escape(text)}</text>'
    )
    return "".join(out)


def _annotation(el, defs, font_family, s=1.0):
    """Boxed label + leader + target dot — the standard scientific callout (membrane diagram).
    `target` is the point on the artwork; `at` is where the label sits; `side` decides which box
    edge the leader leaves from. Child renderers get RAW sizes + the real `s` (they scale once)."""
    target = tuple(el["target"])
    at = tuple(el["at"])
    side = el.get("side", "left" if at[0] < target[0] else "right")
    fs = float(el.get("font_size", 14)) * s  # scaled — for THIS function's geometry only
    text = str(el.get("text", ""))
    weight = str(el.get("weight", "600"))
    pad_x = 9.0 * s
    bw = _approx_text_w(text, fs, weight) + 2 * pad_x
    # leader starts at the inner edge of the box, midline
    edge_x = at[0] + (bw / 2 if side == "left" else -bw / 2)
    start = (edge_x, at[1])
    body = []
    body.append(
        _label(
            {
                "text": text,
                "x": at[0],
                "y": at[1],
                "anchor": "middle",
                "font_size": el.get("font_size", 14),
                "weight": weight,
                "color": el.get("color", "#1f2937"),
                "box": el.get(
                    "box",
                    {
                        "fill": "#ffffff",
                        "stroke": "#d1d5db",
                        "radius": 6,
                        "shadow": el.get("shadow", False),
                    },
                ),
            },
            defs,
            font_family,
            s,
        )
    )
    body.insert(
        0,
        _leader(
            {
                "from": list(start),
                "to": list(target),
                "route": el.get("route", "elbow"),
                "color": el.get("leader_color", "#9ca3af"),
                "width": el.get("leader_width", 1.2),
                "dash": el.get("leader_dash", ""),
                "dot": el.get("dot", True),
                "dot_color": el.get("dot_color", "#4b5563"),
            },
            defs,
            s,
        ),
    )
    return "".join(body)


def _panel(el, defs, s=1.0):
    x, y, w, h = float(el["x"]), float(el["y"]), float(el["w"]), float(el["h"])
    rad = float(el.get("radius", 12)) * s
    fill = el.get("fill", "none")
    stroke = el.get("stroke", "#9ca3af")
    sw = float(el.get("stroke_width", 1.4)) * s
    dash = f' stroke-dasharray="{el.get("dash", "")}"' if el.get("dash") else ""
    out = (
        f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="{_f(rad)}" '
        f'ry="{_f(rad)}" fill="{escape(fill)}" stroke="{escape(stroke)}" stroke-width="{_f(sw)}"{dash}/>'
    )
    if el.get("title"):
        out += _label(
            {
                "text": el["title"],
                "x": x + w / 2,
                "y": y,
                "anchor": "middle",
                "font_size": el.get("title_size", 13),
                "weight": "700",
                "color": el.get("title_color", "#374151"),
                "box": {
                    "fill": el.get("title_bg", "#ffffff"),
                    "stroke": "none",
                    "pad_x": 8,
                    "pad_y": 2,
                    "radius": 4,
                },
            },
            defs,
            DEFAULT_FONT,
            s,
        )
    return out


def _dot(el, defs, s=1.0):
    return (
        f'<circle cx="{_f(el["x"])}" cy="{_f(el["y"])}" r="{_f(float(el.get("r", 3)) * s)}" '
        f'fill="{escape(el.get("fill", "#374151"))}"/>'
    )


# ===========================================================================
# flowchart node: rounded box + optional embedded icon + wrapped text — the
# "icons, not just boxes" look, born as editable vector (rect + <image> + <text>).
# ===========================================================================
def _wrap_lines(text: str, max_w: float, fs: float, weight: str) -> list:
    """Greedy word-wrap to fit max_w px (cheap estimate — see _approx_text_w)."""
    words = str(text).split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if _approx_text_w(trial, fs, weight) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _multiline_text(lines, cx, cy, fs, weight, color, ff, line_h) -> str:
    """Centered multi-line <text>, vertically centred on cy."""
    total = (len(lines) - 1) * line_h
    y0 = cy - total / 2
    out = []
    for i, ln in enumerate(lines):
        y = y0 + i * line_h + fs * 0.35
        out.append(
            f'<text x="{_f(cx)}" y="{_f(y)}" font-family="{ff}" font-size="{_f(fs)}" '
            f'font-weight="{weight}" fill="{escape(color)}" text-anchor="middle">{escape(ln)}</text>'
        )
    return "".join(out)


def _icon_image(path, x, y, w, h) -> str:
    """Embed an icon file as a base64 <image> (self-contained SVG). Missing file -> nothing (never fatal)."""
    try:
        data = Path(path).read_bytes()
    except Exception:
        return ""
    mime = _IMG_MIME.get(Path(path).suffix.lower(), "image/png")
    b64 = base64.b64encode(data).decode()
    return (
        f'<image x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" '
        f'preserveAspectRatio="xMidYMid meet" xlink:href="data:{mime};base64,{b64}"/>'
    )


def _node(el, defs, font_family, s=1.0) -> str:
    """A flowchart node = rounded box + optional icon (top) + wrapped label, plus an optional step
    badge. Everything is editable vector; the icon is an embedded <image>. Coords/size (x,y,w,h) are in
    pixel space; style sizes (stroke, radius, pad, font) scale with `s`."""
    x, y, w, h = float(el["x"]), float(el["y"]), float(el["w"]), float(el["h"])
    rad = float(el.get("radius", 12)) * s
    fill = el.get("fill", "#ffffff")
    stroke = el.get("stroke", "#94a3b8")
    sw = float(el.get("stroke_width", 1.6)) * s
    ff = el.get("font_family", font_family)
    filt = f' filter="url(#{defs.shadow()})"' if el.get("shadow", True) else ""
    out = [
        f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="{_f(rad)}" ry="{_f(rad)}" '
        f'fill="{escape(fill)}" stroke="{escape(stroke)}" stroke-width="{_f(sw)}"{filt}/>'
    ]
    pad = float(el.get("pad", 10)) * s
    fs = float(el.get("font_size", 13)) * s
    weight = str(el.get("weight", "600"))
    tcolor = el.get("text_color", "#1f2937")
    line_h = fs * 1.25
    lines = _wrap_lines(el.get("text", ""), w - 2 * pad, fs, weight)
    icon = el.get("icon")
    if icon and lines:
        icon_h = (h - 2 * pad) * 0.58
        iw = min(w - 2 * pad, icon_h)
        out.append(_icon_image(icon, x + (w - iw) / 2, y + pad, iw, icon_h))
        tcy = y + pad + icon_h + (h - 2 * pad - icon_h) / 2
        out.append(_multiline_text(lines, x + w / 2, tcy, fs, weight, tcolor, ff, line_h))
    elif icon:
        icon_h = h - 2 * pad
        iw = min(w - 2 * pad, icon_h)
        out.append(_icon_image(icon, x + (w - iw) / 2, y + pad, iw, icon_h))
    elif lines:
        out.append(_multiline_text(lines, x + w / 2, y + h / 2, fs, weight, tcolor, ff, line_h))
    if el.get("step") is not None:
        r = fs * 0.9
        cx, cy = x + r + 3 * s, y + r + 3 * s
        out.append(
            f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(r)}" fill="{escape(el.get("step_bg", stroke))}"/>'
        )
        out.append(
            f'<text x="{_f(cx)}" y="{_f(cy + r * 0.35)}" font-family="{ff}" font-size="{_f(r * 1.05)}" '
            f'font-weight="700" fill="{escape(el.get("step_color", "#ffffff"))}" '
            f'text-anchor="middle">{escape(str(el["step"]))}</text>'
        )
    return "".join(out)


_RENDERERS = {
    "arrow": lambda el, defs, ff, s: _arrow(el, defs, s),
    "leader": lambda el, defs, ff, s: _leader(el, defs, s),
    "label": lambda el, defs, ff, s: _label(el, defs, ff, s),
    "annotation": lambda el, defs, ff, s: _annotation(el, defs, ff, s),
    "panel": lambda el, defs, ff, s: _panel(el, defs, s),
    "node": lambda el, defs, ff, s: _node(el, defs, ff, s),
    "dot": lambda el, defs, ff, s: _dot(el, defs, s),
    "raw": lambda el, defs, ff, s: str(el.get("svg", "")),
}


# ===========================================================================
# top-level builders
# ===========================================================================
def build_inner(spec) -> tuple[str, str, int, int]:
    """Return (defs, body, width, height) — used when embedding the overlay inside another SVG
    (the layered-export case in compose_figure_layers)."""
    w = int(spec.get("width", 1920))
    h = int(spec.get("height", 1080))
    ff = spec.get("font_family", DEFAULT_FONT)
    # Resolution-aware sizing: elements are authored in ~1024px REFERENCE units, so all style sizes
    # (fonts, strokes, dots, pads) scale up on a larger canvas — otherwise a 14px label is invisible on
    # a 2K/4K artwork. Coordinates (x,y,w,h,points) stay in the artwork's real pixel space. A caller can
    # pin it with spec["scale"]; otherwise it's derived from the longer canvas side.
    s = float(spec["scale"]) if spec.get("scale") else max(1.0, max(w, h) / 1024.0)
    defs = _Defs()
    body = []
    for el in spec.get("elements", []):
        kind = el.get("kind")
        fn = _RENDERERS.get(kind)
        if fn is None:
            raise ValueError(f"unknown overlay element kind: {kind!r}")
        body.append(fn(el, defs, ff, s))
    return defs.render(), "".join(body), w, h


def build_svg(spec) -> str:
    """Return a complete standalone <svg> string for the overlay spec."""
    defs, body, w, h = build_inner(spec)
    bg = spec.get("background")
    rect = f'<rect width="{w}" height="{h}" fill="{escape(bg)}"/>' if bg else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">{defs}{rect}{body}</svg>'
    )
