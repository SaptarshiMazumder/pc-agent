"""figures_labels.py — deterministic label placement (pure stdlib; no agentd imports).

Turns anchor points (from extract_anchors) into clean, non-overlapping label callouts. Unlike the
old "push everything to the left/right margin" packer, the default engine places each label in the
BEST free spot in a ring AROUND its own structure — above, below, beside, wherever there is
whitespace — with a short, non-crossing leader. Output is render_overlay-ready elements.

Strategies (`strategy`):
  • "auto"     (default) — ring-candidate placement near each structure; a label only spills to a
                 side margin when its whole neighbourhood is occupied (margin slots are just more
                 candidates, costed by their long leaders, so they win only when nothing near is free).
  • "adjacent" — ring placement only, never spill (best for sparse figures with room around things).
  • "callout"  — the classic two-column margin packer (PAVA de-overlap); good for a dense central
                 illustration framed by clear side margins (the membrane-diagram look).

Occupancy awareness (optional — this is what makes "place wherever there's room" real):
  • occupancy       — a coarse boolean grid of where the ARTWORK is drawn (from the white-background
                       mask the place_labels tool computes). Labels avoid covering the drawing.
  • structure_boxes — per-structure [x,y,w,h] regions so leaders avoid crossing OTHER structures and
                       boxes sit just outside the target's own footprint.

Geometry is deterministic: greedy seed (hardest anchor first) + a few bounded local-search passes
with fixed tie-breaks — no RNG, so the same inputs always give the same figure.
"""

from __future__ import annotations

import math

from figures_overlay import _approx_text_w   # reuse the renderer's text-width estimate

INF = float("inf")
_PAD_X, _PAD_Y = 9.0, 5.0   # must match figures_overlay._label box padding so overlap≈render

# 8 compass directions (unit vectors). A label can leave its structure on any of them.
_S = math.sqrt(0.5)
_DIRS = [(1, 0), (-1, 0), (0, -1), (0, 1), (_S, -_S), (-_S, -_S), (_S, _S), (-_S, _S)]


# ===========================================================================
# geometry helpers
# ===========================================================================
def _box_size(text, font_size, weight):
    return (_approx_text_w(str(text), font_size, weight) + 2 * _PAD_X, font_size + 2 * _PAD_Y)


def _overlap_area(a, b):
    """Intersection area of two (x, y, w, h) rects (0 if disjoint)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy


def _in_bounds(rect, W, H, m=4):
    x, y, w, h = rect
    return x >= m and y >= m and x + w <= W - m and y + h <= H - m


def _seg_cross(p, p2, q, q2):
    """True if segment p->p2 properly intersects q->q2."""
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = ccw(q, q2, p), ccw(q, q2, p2)
    d3, d4 = ccw(p, p2, q), ccw(p, p2, q2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _seg_hits_rect(a, b, rect):
    x, y, w, h = rect
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    return any(_seg_cross(a, b, e[0], e[1]) for e in edges)


def _occupied_frac(occ, rect):
    """Fraction of `rect` covered by drawn artwork, per the coarse occupancy grid (0..1)."""
    if not occ:
        return 0.0
    cell = occ["cell"]
    grid = occ["grid"]
    rows, cols = len(grid), len(grid[0]) if grid else 0
    if cols == 0:
        return 0.0
    x, y, w, h = rect
    gx0, gy0 = max(0, int(x // cell)), max(0, int(y // cell))
    gx1, gy1 = min(cols - 1, int((x + w) // cell)), min(rows - 1, int((y + h) // cell))
    if gx1 < gx0 or gy1 < gy0:
        return 0.0
    total = occupied = 0
    for gy in range(gy0, gy1 + 1):
        row = grid[gy]
        for gx in range(gx0, gx1 + 1):
            total += 1
            if row[gx]:
                occupied += 1
    return occupied / total if total else 0.0


# ===========================================================================
# candidate generation
# ===========================================================================
def _candidates(anchor, W, H, font_size, weight, box, *, inset, top, bottom,
                radii, with_margins, gap):
    """All possible placements for one anchor: a ring of positions around the target, plus (when
    `with_margins`) a few left/right margin slots that act as the spill-over fallback."""
    text = str(anchor["label"])
    bw, bh = _box_size(text, font_size, weight)
    tx, ty = float(anchor["target"][0]), float(anchor["target"][1])
    foot = anchor.get("box_region")           # [x,y,w,h] of the structure itself, if known
    side_hint = anchor.get("side")
    cands = []

    for (dx, dy) in _DIRS:
        he = abs(dx) * bw / 2 + abs(dy) * bh / 2     # box half-extent along this direction
        # start clearance from the structure's edge if we know its footprint, else from the point
        clear = 0.0
        if foot:
            fx, fy, fw, fh = foot
            clear = abs(dx) * fw / 2 + abs(dy) * fh / 2
        for r in radii:
            cx = tx + dx * (clear + r + he)
            cy = ty + dy * (clear + r + he)
            rect = (cx - bw / 2, cy - bh / 2, bw, bh)
            attach = (cx - dx * he, cy - dy * he)     # point on the box edge toward the target
            side = "left" if dx < -0.3 else ("right" if dx > 0.3 else side_hint or "right")
            cands.append({"rect": rect, "cx": cx, "cy": cy, "anchor": "middle",
                          "attach": attach, "leader_len": math.hypot(cx - tx, cy - ty),
                          "side": side, "kind": "ring", "dir": (dx, dy)})

    if with_margins:
        for side in ("left", "right"):
            for k in (0, 1, -1, 2, -2, 3, -3):
                cy = min(max(ty + k * gap, top), bottom)
                if side == "left":
                    cx = inset + bw / 2
                    attach = (inset + bw, cy)
                else:
                    cx = W - inset - bw / 2
                    attach = (W - inset - bw, cy)
                rect = (cx - bw / 2, cy - bh / 2, bw, bh)
                cands.append({"rect": rect, "cx": cx, "cy": cy, "anchor": "middle",
                              "attach": attach, "leader_len": math.hypot(cx - tx, cy - ty),
                              "side": side, "kind": "margin", "dir": (-1 if side == "left" else 1, 0)})
    return cands


# ===========================================================================
# cost + solver
# ===========================================================================
def _cost(cand, anchor, placed, W, H, occ, structure_boxes, self_idx):
    rect, attach = cand["rect"], cand["attach"]
    target = (float(anchor["target"][0]), float(anchor["target"][1]))
    if not _in_bounds(rect, W, H):
        return INF
    cost = 0.0
    # short leaders preferred (ring beats margin unless ring spots are bad)
    cost += cand["leader_len"] * 0.45
    cost += 14.0 if cand["kind"] == "margin" else 0.0
    # don't cover the artwork
    cost += _occupied_frac(occ, rect) * 900.0
    # don't cover / collide with other labels (near-hard)
    for p in placed:
        if p is None:
            continue
        cost += _overlap_area(rect, p["rect"]) * 6.0
        pt = p.get("target")
        if pt is not None and _seg_cross(attach, target, p["attach"], (float(pt[0]), float(pt[1]))):
            cost += 70.0
    # don't sit on, or aim a leader through, OTHER structures
    for j, sb in enumerate(structure_boxes or []):
        if j == self_idx or not sb:
            continue
        cost += _overlap_area(rect, sb) * 4.0
        if _seg_hits_rect(attach, target, sb):
            cost += 45.0
    # gentle preference to point outward (away from canvas centre) — reads cleaner
    ox, oy = cand["cx"] - W / 2, cand["cy"] - H / 2
    on = math.hypot(ox, oy) or 1.0
    cost -= (cand["dir"][0] * ox / on + cand["dir"][1] * oy / on) * 7.0
    # honour an explicit side hint
    if anchor.get("side") and cand["side"] != anchor["side"]:
        cost += 40.0
    return cost


def _solve(anchors, cand_lists, W, H, occ, structure_boxes, passes=4):
    """Greedy seed (hardest first) then bounded local search. Returns the chosen candidate per
    anchor (a copy, with its leader `target` attached so other anchors can avoid crossing it)."""
    n = len(anchors)
    chosen = [None] * n
    # order: fewest in-bounds candidates first (most constrained), tie-break by target y then x
    feas = [sum(1 for c in cl if _in_bounds(c["rect"], W, H)) for cl in cand_lists]
    order = sorted(range(n), key=lambda i: (feas[i], anchors[i]["target"][1], anchors[i]["target"][0]))

    def best_for(i, placed):
        best, best_c = INF, None
        for c in cand_lists[i]:
            k = _cost(c, anchors[i], placed, W, H, occ, structure_boxes, i)
            if k < best:
                best, best_c = k, c
        if best_c is None:
            return None, INF
        out = dict(best_c)
        out["target"] = anchors[i]["target"]
        return out, best

    for i in order:
        chosen[i], _ = best_for(i, chosen)

    for _ in range(passes):
        moved = False
        for i in order:
            others = [chosen[j] if j != i else None for j in range(n)]
            cur = (_cost(chosen[i], anchors[i], others, W, H, occ, structure_boxes, i)
                   if chosen[i] is not None else INF)
            cand, k = best_for(i, others)
            if cand is not None and k < cur - 1e-6:
                chosen[i] = cand
                moved = True
        if not moved:
            break
    return chosen


# ===========================================================================
# public API
# ===========================================================================
def place(width, height, anchors, *, strategy="auto", occupancy=None, structure_boxes=None,
          inset=28, font_size=15, gap=None, weight="600", top=None, bottom=None, stub=22,
          leader_color="#9ca3af", leader_dash="", leader_head="none", box=None):
    """Return {'elements': [...render_overlay...], 'placements': [...], 'width', 'height'}.

    anchors: [{label, target:[x,y], side?, box_region?:[x,y,w,h]}].
    strategy: auto | adjacent | callout (see module docstring).
    occupancy: {'cell':int,'grid':[[0|1,...],...]} artwork mask, or None.
    structure_boxes: list aligned to anchors of [x,y,w,h] (or None entries), or None.
    """
    width, height = int(width), int(height)
    top = int(height * 0.05) if top is None else top
    bottom = int(height * 0.95) if bottom is None else bottom
    gap = (font_size + 16) if gap is None else gap
    box = box or {"fill": "#ffffff", "stroke": "#d1d5db", "radius": 6, "shadow": True}

    if strategy == "callout":
        return _place_margins(width, height, anchors, inset=inset, font_size=font_size, gap=gap,
                              weight=weight, top=top, bottom=bottom, stub=stub,
                              leader_color=leader_color, leader_dash=leader_dash,
                              leader_head=leader_head, box=box)

    # align structure footprints to anchors (explicit list wins, else read per-anchor box_region)
    sboxes = structure_boxes if structure_boxes is not None else [a.get("box_region") for a in anchors]
    annotated = []
    for a, sb in zip(anchors, sboxes):
        aa = dict(a)
        if sb is not None:
            aa["box_region"] = sb
        annotated.append(aa)

    radii = (24.0, 64.0, 120.0) if strategy == "auto" else (20.0, 52.0, 96.0, 150.0)
    with_margins = (strategy == "auto")
    cand_lists = [_candidates(a, width, height, font_size, weight, box, inset=inset,
                              top=top, bottom=bottom, radii=radii, with_margins=with_margins, gap=gap)
                  for a in annotated]
    chosen = _solve(annotated, cand_lists, width, height, occupancy, sboxes)

    elements, placements = [], []
    for i, (a, c) in enumerate(zip(annotated, chosen)):
        if c is None:                # no in-bounds spot (adjacent strategy): take the shortest leader
            c = dict(min(cand_lists[i], key=lambda z: z["leader_len"]))
        text = str(a["label"])
        elements.append({"kind": "label", "text": text, "x": c["cx"], "y": c["cy"],
                         "anchor": "middle", "font_size": font_size, "weight": weight,
                         "box": dict(box)})
        leader = {"kind": "leader", "points": [list(c["attach"]), list(a["target"])],
                  "route": "straight", "color": leader_color, "width": 1.2, "dash": leader_dash,
                  "dot_color": "#4b5563"}
        if leader_head in ("standard", "soft"):
            leader["head"] = leader_head
            leader["dot"] = False
        else:
            leader["dot"] = True
        elements.append(leader)
        placements.append({"label": text, "at": [round(c["cx"], 1), round(c["cy"], 1)],
                           "target": list(a["target"]), "side": c["side"], "kind": c["kind"]})
    return {"elements": elements, "placements": placements, "width": width, "height": height}


# ===========================================================================
# legacy two-column margin packer (the "callout" strategy) — exact PAVA de-overlap
# ===========================================================================
def _pava(desired, gap):
    """Positions p (ascending, p[i+1]-p[i] >= gap) closest in L2 to `desired` (already sorted)."""
    if not desired:
        return []
    e = [d - i * gap for i, d in enumerate(desired)]
    blocks = []
    for v in e:
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0] - 1e-9:
            (v2, w2), (v1, w1) = blocks.pop(), blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2])
    q = []
    for mean, w in blocks:
        q.extend([mean] * w)
    return [qq + i * gap for i, qq in enumerate(q)]


def _fit(ys, lo, hi):
    if not ys:
        return ys
    if ys[0] < lo:
        ys = [y + (lo - ys[0]) for y in ys]
    if ys[-1] > hi:
        ys = [y - (ys[-1] - hi) for y in ys]
    return ys


def _place_margins(width, height, anchors, *, inset, font_size, gap, weight, top, bottom, stub,
                   leader_color, leader_dash, leader_head, box):
    elements, placements = [], []
    for side in ("left", "right"):
        group = [a for a in anchors
                 if (a.get("side") or ("left" if a["target"][0] < width / 2 else "right")) == side]
        group.sort(key=lambda a: a["target"][1])
        desired = [min(max(float(a["target"][1]), top), bottom) for a in group]
        ys = _fit(_pava(desired, gap), top, bottom)

        for a, y in zip(group, ys):
            text = str(a["label"])
            bw = _approx_text_w(text, font_size, weight) + 18
            if side == "left":
                lx = inset
                edge = inset + bw
                pts = [[edge, y], [edge + stub, y], list(a["target"])]
                anchor = "start"
            else:
                lx = width - inset
                edge = width - inset - bw
                pts = [[edge, y], [edge - stub, y], list(a["target"])]
                anchor = "end"
            elements.append({"kind": "label", "text": text, "x": lx, "y": y, "anchor": anchor,
                             "font_size": font_size, "weight": weight, "box": dict(box)})
            leader = {"kind": "leader", "points": pts, "route": "elbow", "corner_radius": 6,
                      "color": leader_color, "width": 1.2, "dash": leader_dash, "dot_color": "#4b5563"}
            if leader_head in ("standard", "soft"):
                leader["head"] = leader_head
                leader["dot"] = False
            else:
                leader["dot"] = True
            elements.append(leader)
            placements.append({"label": text, "at": [round(lx, 1), round(y, 1)],
                               "target": list(a["target"]), "side": side, "kind": "margin"})
    return {"elements": elements, "placements": placements, "width": width, "height": height}
