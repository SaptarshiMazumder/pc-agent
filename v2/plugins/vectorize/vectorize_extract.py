"""vectorize_extract.py — deterministic annotation-layer extraction core (no agentd imports).

Given a LABELLED image L and a pixel-aligned TEXTLESS base T' (T' derived FROM L by stripping, so
alignment is guaranteed), everything the image model drew as annotation — text glyphs, leader
lines, arrows — is exactly diff(L, T'). This module does the pixel work of turning that diff into
overlay-ready geometry:

  • diff_mask          — |L - T'| threshold + closing -> the annotation mask (+ coverage fraction,
                         the alignment gate: a huge fraction means the strip drifted).
  • components         — connected annotation blobs (each one glyph, leader, or arrow).
  • trace_stroke       — one blob -> ordered centerline waypoints (skeleton + BFS diameter walk),
                         stroke width (medial-axis distance), and per-end ARROWHEAD detection
                         (width-profile test: a head is a monotonic widening at one end).
  • component_color    — median ink colour of a blob, sampled from L.

Positions come from pixels, not from a VLM reading coordinates — that is the whole point: label
and arrow placement is correct by construction. Pure numpy + scikit-image + Pillow; the OCR and
element assembly live in the tool (extract_annotations_tool.py).
"""

from __future__ import annotations

import math

# Neighbour offsets for 8-connectivity, used by the skeleton walker.
_N8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def _require_deps():
    """Import the heavy optional deps with one actionable error (pattern: trace_image)."""
    try:
        import numpy  # noqa: F401
        import skimage  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "annotation extraction needs numpy + scikit-image "
            "(pip install 'agentd[figures-vector]' or: pip install numpy scikit-image)"
        ) from e


# ===========================================================================
# diff mask + components
# ===========================================================================
def diff_mask(labeled_rgb, base_rgb, thr: int = 40):
    """Boolean annotation mask = where L differs from T' (max channel |Δ| > thr), sealed with a
    small closing so anti-aliased strokes come out as single solid components.
    Returns (mask, fraction) — fraction is the alignment gate (big => strip drifted)."""
    import numpy as np
    from skimage.morphology import closing, disk

    a = np.asarray(labeled_rgb, dtype=np.int16)[..., :3]
    b = np.asarray(base_rgb, dtype=np.int16)[..., :3]
    d = np.abs(a - b).max(axis=-1)
    mask = d > thr
    frac = float(mask.mean())
    mask = closing(mask, disk(1))  # (grey `closing` on a bool array == binary closing)
    return mask, frac


def components(mask, min_area: int = 12):
    """Connected components of the annotation mask -> list of dicts:
    {label, bbox (x0,y0,x1,y1), area, coords (N,2 array of [y,x])} — tiny specks dropped."""
    import numpy as np
    from skimage.measure import label as sk_label
    from skimage.measure import regionprops

    lab = sk_label(mask, connectivity=2)
    out = []
    for rp in regionprops(lab):
        if rp.area < min_area:
            continue
        y0, x0, y1, x1 = rp.bbox
        out.append(
            {
                "label": int(rp.label),
                "bbox": (int(x0), int(y0), int(x1), int(y1)),
                "area": int(rp.area),
                "coords": np.asarray(rp.coords),
            }
        )
    return out


def component_color(labeled_rgb, coords) -> str:
    """Median ink colour of a component, sampled from the LABELLED image (#rrggbb)."""
    import numpy as np

    a = np.asarray(labeled_rgb)[..., :3]
    px = a[coords[:, 0], coords[:, 1]]
    med = np.median(px, axis=0).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*(int(v) for v in med))


# ===========================================================================
# skeleton walking (bespoke — no numba/sknw; annotation density is tiny)
# ===========================================================================
def _skeleton_graph(skel):
    """Skeleton bool array -> ({(y,x): [neighbours...]}, endpoints). 8-connected."""
    import numpy as np

    pts = set(map(tuple, np.argwhere(skel)))
    adj = {}
    for y, x in pts:
        nbrs = [(y + dy, x + dx) for dy, dx in _N8 if (y + dy, x + dx) in pts]
        adj[(y, x)] = nbrs
    ends = [p for p, nb in adj.items() if len(nb) == 1]
    return adj, ends


def _bfs_farthest(adj, start):
    """BFS from `start` -> (farthest node, path to it). Works on any connected graph;
    on the (near-)tree skeleton of a stroke this walks out to the diameter end."""
    from collections import deque

    prev = {start: None}
    q = deque([start])
    last = start
    while q:
        cur = q.popleft()
        last = cur
        for nb in adj[cur]:
            if nb not in prev:
                prev[nb] = cur
                q.append(nb)
    path = []
    node = last
    while node is not None:
        path.append(node)
        node = prev[node]
    path.reverse()
    return last, path


def _diameter_path(adj):
    """Longest shortest-path through the skeleton (double-BFS): the stroke's centerline,
    naturally ignoring the short branches an arrowhead's corners create."""
    start = next(iter(adj))
    e1, _ = _bfs_farthest(adj, start)
    _, path = _bfs_farthest(adj, e1)
    return path


def douglas_peucker(pts, eps: float):
    """Classic DP polyline simplification (iterative). pts = [(x, y), ...]."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        ax, ay = pts[i0]
        bx, by = pts[i1]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1.0
        worst, worst_d = -1, 0.0
        for i in range(i0 + 1, i1):
            px, py = pts[i]
            d = abs((px - ax) * dy - (py - ay) * dx) / L
            if d > worst_d:
                worst, worst_d = i, d
        if worst_d > eps and worst > 0:
            keep[worst] = True
            stack.append((i0, worst))
            stack.append((worst, i1))
    return [p for p, k in zip(pts, keep) if k]


# ===========================================================================
# stroke tracing + arrowhead detection
# ===========================================================================
def trace_stroke(coords, bbox, shape, head_ratio: float = 1.9, head_abs: float = 1.5):
    """Trace ONE annotation component into a centerline + width + arrowheads.

    coords = (N,2) [y,x] pixels of the component; bbox = (x0,y0,x1,y1); shape = full image (H,W).
    Returns None when the blob isn't a stroke (a loop, a filled shape, a glyph cluster):
      {"points": [(x,y)...],          # full-image coords, tail..head order NOT yet resolved
       "width": float,                # full stroke width (2 * median medial-axis half-width)
       "head_start": bool, "head_end": bool,   # arrowhead detected at points[0] / points[-1]
       "length": float}               # centerline length in px

    Arrowhead test (width-profile): a head is a clear widening of the medial-axis distance near
    one end vs the stroke's median half-width — cheap and dependency-free, per the parity plan.
    A blob whose area is far above (length x width) is a FILLED shape, not a stroke -> None.
    """
    import numpy as np
    from skimage.morphology import medial_axis, skeletonize

    x0, y0, x1, y1 = bbox
    sub = np.zeros((y1 - y0 + 2, x1 - x0 + 2), dtype=bool)  # 1px pad so skeleton has room
    sub[coords[:, 0] - y0, coords[:, 1] - x0] = True

    skel = skeletonize(sub)
    if not skel.any():
        return None
    _, dist = medial_axis(sub, return_distance=True)

    adj, ends = _skeleton_graph(skel)
    if not ends:  # a loop (circle/ellipse marker) — not a stroke
        return None
    path = _diameter_path(adj)
    if len(path) < 4:
        return None

    d_along = np.array([dist[p] for p in path], dtype=float)
    n = len(path)
    mid = d_along[int(n * 0.25) : max(int(n * 0.75), int(n * 0.25) + 1)]
    half_w = float(np.median(mid)) if mid.size else float(np.median(d_along))
    half_w = max(half_w, 0.5)

    # filled-shape rejection: a stroke's area ≈ length * width (allow head + rounding slack)
    length = float(sum(math.hypot(b[1] - a[1], b[0] - a[0]) for a, b in zip(path, path[1:])))
    if length < 6:
        return None
    expected_area = length * (2 * half_w)
    if len(coords) > max(expected_area * 2.4, expected_area + 120):
        return None

    k = int(min(max(6, half_w * 6), max(6, n // 3)))

    def _widens(seg) -> bool:
        peak = float(seg.max()) if seg.size else 0.0
        return peak > max(half_w * head_ratio, half_w + head_abs)

    head_start = _widens(d_along[:k])
    head_end = _widens(d_along[-k:])

    # sub-space [y,x] -> full-image [x,y]
    pts = [(float(x + x0 - 1), float(y + y0 - 1)) for (y, x) in path]
    return {
        "points": pts,
        "width": round(2 * half_w, 2),
        "head_start": bool(head_start),
        "head_end": bool(head_end),
        "length": round(length, 1),
    }


def simplify_waypoints(pts, width: float, max_points: int = 7):
    """Centerline pixels -> a few waypoints for the overlay engine. 2 points => route 'straight';
    more => route 'curved' (the engine draws a Catmull-Rom through them). eps scales with stroke
    width so a chunky arrow doesn't sprout waypoints from its own jitter."""
    eps = max(1.2, width * 0.6)
    simp = douglas_peucker(pts, eps)
    while len(simp) > max_points:
        eps *= 1.6
        simp = douglas_peucker(pts, eps)
    route = "straight" if len(simp) == 2 else "curved"
    simp = [(round(x, 1), round(y, 1)) for x, y in simp]
    return simp, route


# ===========================================================================
# canvas check (deterministic white-background gate)
# ===========================================================================
def white_background_fraction(img_rgb, border_px: int = 8, thr: int = 245) -> float:
    """Fraction of the image's border band that is (near-)pure white — the deterministic check
    for the house invariant (pure-white background, no cast shadow bleeding to the edges)."""
    import numpy as np

    a = np.asarray(img_rgb)[..., :3]
    h, w = a.shape[:2]
    b = min(border_px, h // 2, w // 2)
    band = np.concatenate(
        [
            a[:b, :, :].reshape(-1, 3),
            a[-b:, :, :].reshape(-1, 3),
            a[:, :b, :].reshape(-1, 3),
            a[:, -b:, :].reshape(-1, 3),
        ]
    )
    return float((band.min(axis=-1) >= thr).mean())
