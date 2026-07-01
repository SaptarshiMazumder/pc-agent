"""routing.py — graph layout + edge routing (pure stdlib, no agentd imports).

Two jobs, both feeding the overlay engine's `arrow` elements:

  1. LAYOUT — when nodes have no x/y, place them in layers (Sugiyama-style longest-path layering
     + barycenter ordering). Enough for flows/pathways drawn ON TOP of artwork, where we control
     placement (the *structured* path instead delegates layout to Mermaid/PlantUML).

  2. ROUTING — given node rectangles (placed or pre-positioned, e.g. from a vision model locating
     structures on the image), compute each edge's waypoint list:
        straight     -> [src_border, tgt_border]
        orthogonal   -> elbow with 1-2 bends, leaving/entering the natural sides
        curved       -> [src_border, tgt_border] (the overlay bows it)
     Endpoints are clipped to the node BORDER (not the center) so arrowheads kiss the box edge.

Output is JSON-friendly and directly consumable by render_overlay:
    {"nodes": [{"id","x","y","w","h","cx","cy"}],
     "edges": [{"from","to","points":[[x,y],...],"route"}]}
"""

from __future__ import annotations

from collections import defaultdict, deque

DEFAULT_W, DEFAULT_H = 120.0, 48.0


def _as_node(n):
    return {
        "id": n["id"],
        "x": n.get("x"),
        "y": n.get("y"),
        "w": float(n.get("w", DEFAULT_W)),
        "h": float(n.get("h", DEFAULT_H)),
    }


# ===========================================================================
# layout (only runs when positions are missing)
# ===========================================================================
def _layer_assignment(ids, edges):
    """Longest-path layering: layer(v) = longest chain of edges ending at v. Cycles are broken
    implicitly by only relaxing forward, so a back-edge just doesn't push its target deeper."""
    succ = defaultdict(list)
    indeg = {i: 0 for i in ids}
    for e in edges:
        if e["from"] in indeg and e["to"] in indeg:
            succ[e["from"]].append(e["to"])
            indeg[e["to"]] += 1
    layer = {i: 0 for i in ids}
    q = deque([i for i in ids if indeg[i] == 0]) or deque(ids)
    seen = set()
    while q:
        u = q.popleft()
        if u in seen:
            continue
        seen.add(u)
        for v in succ[u]:
            if layer[v] < layer[u] + 1:
                layer[v] = layer[u] + 1
            indeg[v] -= 1
            if indeg[v] <= 0:
                q.append(v)
    return layer


def _order_within_layers(layers, edges):
    """One barycenter sweep: order each layer by the average index of its predecessors, which
    reduces edge crossings versus naive input order. Good enough for the small graphs we draw."""
    pred = defaultdict(list)
    for e in edges:
        pred[e["to"]].append(e["from"])
    ordered = {}
    prev_index = {}
    for li in sorted(layers):
        nodes = layers[li]
        if not prev_index:
            ordered[li] = nodes
        else:
            def bary(n):
                ps = [prev_index[p] for p in pred[n] if p in prev_index]
                return sum(ps) / len(ps) if ps else 0.0
            ordered[li] = sorted(nodes, key=bary)
        for idx, n in enumerate(ordered[li]):
            prev_index[n] = idx
    return ordered


def _place(nodes, edges, direction, node_gap, layer_gap):
    """Assign x/y to nodes that lack them, laying layers out along `direction`."""
    ids = [n["id"] for n in nodes]
    by_id = {n["id"]: n for n in nodes}
    layer = _layer_assignment(ids, edges)
    layers = defaultdict(list)
    for i in ids:
        layers[layer[i]].append(i)
    layers = _order_within_layers(layers, edges)

    horizontal = direction in ("RIGHT", "LEFT")
    for li in sorted(layers):
        col = layers[li]
        # cross-axis sizes for centering this layer
        sizes = [(by_id[n]["h"] if horizontal else by_id[n]["w"]) for n in col]
        span = sum(sizes) + node_gap * (len(col) - 1)
        cursor = -span / 2
        for n, s in zip(col, sizes):
            nd = by_id[n]
            main = li * (layer_gap + (DEFAULT_W if horizontal else DEFAULT_H))
            cross = cursor + s / 2
            if direction == "RIGHT":
                nd["cx"], nd["cy"] = main, cross
            elif direction == "LEFT":
                nd["cx"], nd["cy"] = -main, cross
            elif direction == "DOWN":
                nd["cx"], nd["cy"] = cross, main
            else:  # UP
                nd["cx"], nd["cy"] = cross, -main
            cursor += s + node_gap

    # shift everything into positive space and derive top-left x/y
    xs = [n["cx"] for n in nodes]
    ys = [n["cy"] for n in nodes]
    ox = 60 - min(xs)
    oy = 60 - min(ys)
    for n in nodes:
        n["cx"] += ox
        n["cy"] += oy
        n["x"] = n["cx"] - n["w"] / 2
        n["y"] = n["cy"] - n["h"] / 2


def _ensure_centers(nodes):
    for n in nodes:
        if n.get("x") is None or n.get("y") is None:
            return False
        n["cx"] = n["x"] + n["w"] / 2
        n["cy"] = n["y"] + n["h"] / 2
    return True


# ===========================================================================
# routing
# ===========================================================================
def _border_point(node, toward):
    """Where the segment from node center toward `toward` crosses the node's rectangle border."""
    cx, cy = node["cx"], node["cy"]
    hw, hh = node["w"] / 2, node["h"] / 2
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return (cx, cy)
    sx = hw / abs(dx) if dx else float("inf")
    sy = hh / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return (cx + dx * s, cy + dy * s)


def _orthogonal(src, tgt, direction):
    """Elbow waypoints from src center-ish to tgt, exiting/entering the layout's main axis side."""
    s = (src["cx"], src["cy"])
    t = (tgt["cx"], tgt["cy"])
    horizontal = direction in ("RIGHT", "LEFT")
    if horizontal:
        sx = src["x"] + src["w"] if direction == "RIGHT" else src["x"]
        tx = tgt["x"] if direction == "RIGHT" else tgt["x"] + tgt["w"]
        midx = (sx + tx) / 2
        p0 = (sx, s[1])
        p3 = (tx, t[1])
        return [p0, (midx, s[1]), (midx, t[1]), p3]
    else:
        sy = src["y"] + src["h"] if direction == "DOWN" else src["y"]
        ty = tgt["y"] if direction == "DOWN" else tgt["y"] + tgt["h"]
        midy = (sy + ty) / 2
        p0 = (s[0], sy)
        p3 = (t[0], ty)
        return [p0, (s[0], midy), (t[0], midy), p3]


def route(nodes, edges, direction="RIGHT", router="orthogonal",
          node_gap=40.0, layer_gap=120.0):
    """Lay out (if needed) and route. See module docstring for the return shape."""
    direction = direction.upper()
    nodes = [_as_node(n) for n in nodes]
    if not _ensure_centers(nodes):
        _place(nodes, edges, direction, node_gap, layer_gap)
    by_id = {n["id"]: n for n in nodes}

    routed = []
    for e in edges:
        s, t = by_id.get(e["from"]), by_id.get(e["to"])
        if not s or not t:
            continue
        r = e.get("route", router)
        if r == "orthogonal":
            pts = _orthogonal(s, t, direction)
        elif r == "curved":
            pts = [_border_point(s, (t["cx"], t["cy"])), _border_point(t, (s["cx"], s["cy"]))]
        else:  # straight
            pts = [_border_point(s, (t["cx"], t["cy"])), _border_point(t, (s["cx"], s["cy"]))]
        routed.append({"from": e["from"], "to": e["to"],
                       "points": [[round(x, 2), round(y, 2)] for x, y in pts],
                       "route": "elbow" if r == "orthogonal" else r,
                       **{k: e[k] for k in ("label", "color", "style") if k in e}})

    out_nodes = [{"id": n["id"], "x": round(n["x"], 2), "y": round(n["y"], 2),
                  "w": n["w"], "h": n["h"],
                  "cx": round(n["cx"], 2), "cy": round(n["cy"], 2)} for n in nodes]
    maxx = max((n["x"] + n["w"] for n in nodes), default=0) + 60
    maxy = max((n["y"] + n["h"] for n in nodes), default=0) + 60
    return {"nodes": out_nodes, "edges": routed,
            "width": int(round(maxx)), "height": int(round(maxy))}
