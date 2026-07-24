"""arrow_reader — the SEMANTIC arrow layer: a VLM reads the connectors, pixels refine them.

This is the tier that beats a pure tracing vectorizer (FigureLabs traces arrows into dumb blobs;
nobody reconstructs them as real arrows). We ask a vision model to LIST every arrow / connector /
leader line in the figure — its endpoints, its DIRECTION, whether it's curved, single/double
headed, its colour — then we SNAP those approximate endpoints onto the real drawn ink and, where a
stroke can be traced, take the exact centre-line from the pixels. So: meaning + direction from the
model, precise geometry from the image. Endpoints the model can't localise well are fixed by the
snap; connectors the model misses are caught by the blob-trace fallback in the caller.

Pure helper (no agentd imports): the caller supplies a `vlm(prompt) -> text` callable so this stays
provider-agnostic and unit-testable with a stub.
"""

from __future__ import annotations

# Ask in Gemini's native normalized 0-1000 [y, x] convention (converted to pixels by the caller).
PROMPT = """This scientific figure has ARROWS, CONNECTORS and thin LEADER LINES drawn on it
(ignore all TEXT — do not report text). Use the standard normalized image coordinate system:
integers 0-1000, origin top-left.
For EACH arrow / connector / leader line actually drawn, give one object:
- "kind": "arrow" (has an arrowhead) | "leader" (thin line label->part, no head) | "line"
- "from": [y, x]   — the TAIL end (where it starts / the non-head end)
- "to":   [y, x]   — the HEAD end (where the arrowhead is, or the part a leader points at)
- "curved": true|false   — is the line noticeably curved (not straight)?
- "double": true|false   — does it have an arrowhead at BOTH ends?
- "color": "#rrggbb"     — the line's colour (best estimate)
Return ONLY a JSON array, one object per connector actually drawn (omit text, omit the artwork):
[{"kind":"arrow","from":[y,x],"to":[y,x],"curved":false,"double":false,"color":"#374151"}]
All coordinates are integers 0-1000 (NOT pixels)."""


def _clamp01k(v):
    try:
        return max(0.0, min(1000.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _pt_to_px(p, W, H):
    """Normalized 0-1000 [y, x] -> pixel (x, y). None if absent/malformed."""
    if not p or len(p) < 2:
        return None
    ny, nx = _clamp01k(p[0]), _clamp01k(p[1])
    return (nx / 1000.0 * W, ny / 1000.0 * H)


def read_arrows(vlm, parse_json, W: int, H: int) -> list[dict]:
    """Run the VLM and return a list of raw arrow dicts in PIXEL coords:
    {kind, frm:(x,y), to:(x,y), curved, double, color}. Tolerant of a truncated/garbled reply
    (parse_json is expected to salvage) — returns [] rather than raising, so the caller can fall
    back to blob-tracing. `vlm(prompt)->text` and `parse_json(text)->obj` are injected."""
    try:
        raw = vlm(PROMPT)
        parsed = parse_json(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for a in parsed:
        if not isinstance(a, dict):
            continue
        frm = _pt_to_px(a.get("from"), W, H)
        to = _pt_to_px(a.get("to"), W, H)
        if frm is None or to is None:
            continue
        kind = a.get("kind") if a.get("kind") in ("arrow", "leader", "line") else "arrow"
        color = (
            a.get("color")
            if isinstance(a.get("color"), str) and a["color"].startswith("#")
            else None
        )
        out.append(
            {
                "kind": kind,
                "frm": frm,
                "to": to,
                "curved": bool(a.get("curved")),
                "double": bool(a.get("double")),
                "color": color,
            }
        )
    return out


def to_overlay_element(a: dict, points, color: str) -> dict:
    """Turn one read arrow + its resolved centre-line `points` (tail..head, pixel (x,y)) into a
    render_editable_overlay element. `arrow`/`line` with a head become an `arrow`; a `leader`
    becomes a `leader`. Direction is the model's (head at `to`)."""
    route = (
        "curved"
        if (a.get("curved") and len(points) >= 3)
        else ("curved" if a.get("curved") else "straight")
    )
    pts = [[round(x, 1), round(y, 1)] for x, y in points]
    if a["kind"] == "leader":
        return {"kind": "leader", "points": pts, "route": route, "color": color, "dot": False}
    el = {
        "kind": "arrow",
        "points": pts,
        "route": route,
        "color": color,
        "body": "stroked",
        "head": "standard",
    }
    if a.get("double"):
        el["start_head"] = "standard"
    return el
