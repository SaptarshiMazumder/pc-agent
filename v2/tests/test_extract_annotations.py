"""extract_annotations — deterministic extraction of the annotation layer from synthetic figures.

No network, no image model: we PAINT a known base + annotations (leader, curved arrow with a
filled head, a text label) and assert the pixel pipeline recovers them — mask, components,
centerline, width, arrowhead, and (when rapidocr is installed) the OCR'd label element.
Heavy deps are optional extras, so everything importorskips cleanly on a bare env.
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

np = pytest.importorskip("numpy")
pytest.importorskip("skimage")
PIL = pytest.importorskip("PIL")

import vectorize_extract as vx  # noqa: E402  (plugin dirs are on sys.path via conftest)
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

W, H = 800, 600
INK = (55, 65, 81)  # the annotation grey


def _base() -> Image.Image:
    """White canvas + one coloured 'structure' blob (what the artwork would be)."""
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.ellipse([260, 160, 460, 360], fill=(122, 168, 116), outline=(90, 130, 90), width=3)
    return im


def _bezier(p0, p1, p2, n=60):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
        pts.append((x, y))
    return pts


def _labeled(base: Image.Image) -> Image.Image:
    """base + a straight leader, a curved arrow with a filled triangular head, and a text label."""
    im = base.copy()
    d = ImageDraw.Draw(im)
    # 1) straight leader: margin -> structure edge
    d.line([(600, 150), (452, 218)], fill=INK, width=3)
    # 2) curved arrow (quadratic bezier) with a filled head at the structure end
    curve = _bezier((620, 470), (520, 480), (447, 330))
    d.line(curve, fill=INK, width=5, joint="curve")
    # head: triangle at the curve end, pointing along the final tangent
    (ex, ey), (px_, py_) = curve[-1], curve[-6]
    ang = math.atan2(ey - py_, ex - px_)
    L_, half = 20.0, 9.0
    tip = (ex + L_ * math.cos(ang), ey + L_ * math.sin(ang))
    left = (ex + half * math.cos(ang + math.pi / 2), ey + half * math.sin(ang + math.pi / 2))
    right = (ex + half * math.cos(ang - math.pi / 2), ey + half * math.sin(ang - math.pi / 2))
    d.polygon([tip, left, right], fill=INK)
    # 3) text label near the leader tail
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:  # older Pillow: bitmap default only
        font = ImageFont.load_default()
    d.text((560, 118), "Cortex", fill=INK, font=font)
    return im


@pytest.fixture()
def imgs(tmp_path):
    base = _base()
    lab = _labeled(base)
    bp, lp = tmp_path / "base.png", tmp_path / "labeled.png"
    base.save(bp)
    lab.save(lp)
    return SimpleNamespace(base=base, labeled=lab, base_path=bp, labeled_path=lp)


def _strokes(imgs):
    mask, frac = vx.diff_mask(np.asarray(imgs.labeled), np.asarray(imgs.base))
    comps = vx.components(mask)
    out = {}
    for c in comps:
        tr = vx.trace_stroke(c["coords"], c["bbox"], (H, W))
        if tr is None:
            continue
        x0, y0, x1, y1 = c["bbox"]
        midy = (y0 + y1) / 2
        # classify by where we painted them; narrow blobs in the label zone are text glyphs — skip
        if midy < 260 and (x1 - x0) > 40:
            out.setdefault("leader", (c, tr))
        elif midy > 260:
            out.setdefault("arrow", (c, tr))
    return mask, frac, out


def test_diff_mask_is_annotations_only(imgs):
    mask, frac = vx.diff_mask(np.asarray(imgs.labeled), np.asarray(imgs.base))
    assert 0.001 < frac < 0.10  # annotations are a small, nonzero slice of the canvas
    # nothing inside the untouched structure interior should be flagged
    assert not mask[250:270, 330:390].any()


def test_straight_leader_traced(imgs):
    _, _, strokes = _strokes(imgs)
    assert "leader" in strokes
    c, tr = strokes["leader"]
    pts, route = vx.simplify_waypoints(tr["points"], tr["width"])
    assert route == "straight" and len(pts) == 2
    assert not tr["head_start"] and not tr["head_end"]
    assert 1.5 <= tr["width"] <= 5.5
    ends = sorted(pts)  # [(x,y)...] left-to-right
    assert math.hypot(ends[0][0] - 452, ends[0][1] - 218) < 12
    assert math.hypot(ends[1][0] - 600, ends[1][1] - 150) < 12


def test_curved_arrow_head_detected(imgs):
    _, _, strokes = _strokes(imgs)
    assert "arrow" in strokes
    c, tr = strokes["arrow"]
    assert tr["head_start"] != tr["head_end"]  # exactly one widened end
    pts, route = vx.simplify_waypoints(tr["points"], tr["width"])
    assert route == "curved" and len(pts) >= 3
    # the head end is near the structure (the bezier end + head tip ~ (455, 310))
    head_pt = tr["points"][0] if tr["head_start"] else tr["points"][-1]
    assert math.hypot(head_pt[0] - 452, head_pt[1] - 315) < 30


def test_alignment_gate_fires_on_wrong_base(imgs, tmp_path):
    from extract_annotations_tool import ExtractAnnotationsTool

    wrong = Image.new("RGB", (W, H), (30, 30, 30))
    wp = tmp_path / "wrong.png"
    wrong.save(wp)
    tool = ExtractAnnotationsTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(
        tool.execute("t1", {"labeled": str(imgs.labeled_path), "base": str(wp)}, None)
    )
    assert res.is_error and "alignment gate" in res.content[0].text


def test_tool_end_to_end_with_ocr(imgs, tmp_path):
    pytest.importorskip("rapidocr")
    from extract_annotations_tool import ExtractAnnotationsTool

    tool = ExtractAnnotationsTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(
        tool.execute("t1", {"labeled": str(imgs.labeled_path), "base": str(imgs.base_path)}, None)
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    kinds = [e["kind"] for e in r["elements"]]
    assert kinds.count("arrow") >= 1 and kinds.count("leader") >= 1
    labels = [e for e in r["elements"] if e["kind"] == "label"]
    assert labels and any("cortex" in e["text"].lower() for e in labels)
    # spec file written and loadable by render_editable_overlay's elements_path
    import json

    spec = json.loads(Path(r["out_json"]).read_text(encoding="utf-8"))
    assert spec["width"] == W and spec["height"] == H and spec["elements"]
    # the label sits where we drew it (drawn at x=560.., text centered around ~600, y ~132)
    lab = next(e for e in labels if "cortex" in e["text"].lower())
    assert abs(lab["y"] - 140) < 30


def test_strip_verify_detects_echo(monkeypatch):
    """The strip's verification: if the 'stripped' image still holds most of the original text, the
    model echoed the input (strip failed). We stub OCR to isolate the ratio logic."""
    import extract_annotations_tool as et

    labelled, stripped = Path("lab.png"), Path("strip.png")
    # echo — stripped keeps all 4 labels -> NOT removed
    monkeypatch.setattr(et, "_ocr_lines", lambda p: ["a", "b", "c", "d"])
    ok, before, after = et.strip_verify(labelled, stripped)
    assert ok is False and before == 4 and after == 4
    # clean strip — no text left -> removed
    monkeypatch.setattr(et, "_ocr_lines", lambda p: [] if "strip" in str(p) else ["a", "b", "c", "d"])
    ok2, _, after2 = et.strip_verify(labelled, stripped)
    assert ok2 is True and after2 == 0
    # partial (1 of 4 left, 25% <= 45% threshold) -> counts as removed
    monkeypatch.setattr(et, "_ocr_lines", lambda p: ["a"] if "strip" in str(p) else ["a", "b", "c", "d"])
    assert et.strip_verify(labelled, stripped)[0] is True


def test_white_bg_fraction():
    white = np.full((100, 100, 3), 255, dtype=np.uint8)
    assert vx.white_background_fraction(white) > 0.99
    grey = np.full((100, 100, 3), 128, dtype=np.uint8)
    assert vx.white_background_fraction(grey) < 0.01


def test_strip_failure_still_extracts_text(imgs, tmp_path):
    """The robustness fix: if the strip fails completely (base == labelled), OCR-on-L must still
    read the labels, and the base must be cleaned so the baked text doesn't double."""
    pytest.importorskip("rapidocr")
    from extract_annotations_tool import ExtractAnnotationsTool

    # simulate a totally failed strip: hand the LABELLED image itself in as the "base"
    tool = ExtractAnnotationsTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(
        tool.execute(
            "t1", {"labeled": str(imgs.labeled_path), "base": str(imgs.labeled_path)}, None
        )
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    # label still recovered from L directly, despite the base having the baked text
    assert any("cortex" in e["text"].lower() for e in r["elements"] if e["kind"] == "label")
    assert r["strip_left"] >= 1  # detected the label the strip left behind
    # and a cleaned base was written (label blended into its local background), not the raw image
    assert r["base_png"].endswith("_base_clean.png")
    from PIL import Image as _I

    clean = np.asarray(_I.open(r["base_png"]).convert("RGB"))
    # "Cortex" sits on the white margin -> its box blends to white (the local background), and the
    # baked glyph ink is gone from the base
    assert clean[125:140, 565:640].min() >= 245


def test_label_on_artwork_is_not_white_boxed(tmp_path):
    """A label sitting ON coloured artwork (not a white margin) must NOT leave a white/flat patch
    when the strip 'fails' — the regression the user hit. We simulate a failed strip (base ==
    labelled) with a label drawn over a solid green block, and assert the green is preserved."""
    pytest.importorskip("rapidocr")
    from extract_annotations_tool import ExtractAnnotationsTool

    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    green = (60, 150, 70)
    d.rectangle([300, 300, 700, 500], fill=green)  # a solid artwork block
    try:
        font = ImageFont.load_default(size=28)
    except TypeError:
        font = ImageFont.load_default()
    d.text((360, 370), "Region", fill=(20, 20, 20), font=font)  # label ON the block
    p = tmp_path / "on_art.png"
    im.save(p)

    tool = ExtractAnnotationsTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(tool.execute("t", {"labeled": str(p), "base": str(p)}, None))
    assert not res.is_error, res.content[0].text
    base = np.asarray(Image.open(res.details["base_png"]).convert("RGB"))
    # a patch of the green block AWAY from the glyphs must still be green, not white/flat
    patch = base[305:320, 640:690]
    assert abs(int(patch[..., 1].mean()) - green[1]) < 30  # green channel preserved
    assert patch[..., 1].mean() > patch[..., 0].mean() + 20  # still greener than red (not whitened)


def test_parse_json_salvages_truncated_array():
    import vision_gemini as vg

    good = '[{"label": "A", "point": [1, 2]}, {"label": "B", "point": [3, 4]}]'
    assert len(vg.parse_json(good)) == 2
    # truncated mid-third-object (blew up the old parser) -> salvage the 2 complete ones
    truncated = (
        '[{"label": "A", "point": [1, 2]}, {"label": "B", "point": [3, 4]}, {"label": "C", "po'
    )
    out = vg.parse_json(truncated)
    assert isinstance(out, list) and len(out) == 2
    assert [o["label"] for o in out] == ["A", "B"]
