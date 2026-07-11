"""figure_to_svg — the one-call labelled-figure -> editable SVG converter, and its helpers
(deterministic stroke tracing, snap-to-mask, blob-trace). Fully deterministic: the end-to-end runs
with a supplied textless base (no image-model strip) and traces lines from the pixels, so no network.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

np = pytest.importorskip("numpy")
pytest.importorskip("skimage")
pytest.importorskip("PIL")


import arrow_reader as ar  # noqa: E402
import vectorize_extract as vx  # noqa: E402
from PIL import Image  # noqa: E402
from test_extract_annotations import _base, _labeled  # reuse the synthetic figure  # noqa: E402

W, H = 800, 600


# ---- unit: the new helpers -------------------------------------------------------------------
def test_read_arrows_converts_and_orients():
    raw = json.dumps(
        [
            {
                "kind": "arrow",
                "from": [100, 200],
                "to": [900, 800],
                "curved": False,
                "color": "#123456",
            },
            {"kind": "leader", "from": [500, 500], "to": [400, 300]},
            "garbage",  # non-dict is skipped, not fatal
        ]
    )
    out = ar.read_arrows(lambda _p: raw, json.loads, 1000, 1000)
    assert len(out) == 2
    a = out[0]
    assert a["kind"] == "arrow" and a["color"] == "#123456"
    assert a["frm"] == (200.0, 100.0) and a["to"] == (800.0, 900.0)  # [y,x]->(x,y)
    assert out[1]["kind"] == "leader"


def test_read_arrows_tolerates_bad_reply():
    assert ar.read_arrows(lambda _p: "not json", lambda t: json.loads(t), 100, 100) == []
    assert ar.read_arrows(lambda _p: '{"not":"a list"}', json.loads, 100, 100) == []


def test_to_overlay_element():
    a = {"kind": "arrow", "curved": True, "double": True}
    el = ar.to_overlay_element(a, [(0, 0), (5, 5), (10, 0)], "#abcdef")
    assert el["kind"] == "arrow" and el["route"] == "curved"
    assert el["head"] == "standard" and el["start_head"] == "standard"
    assert el["color"] == "#abcdef" and len(el["points"]) == 3


def test_snap_to_mask():
    mask = np.zeros((100, 100), dtype=bool)
    mask[50, 60] = True
    x, y = vx.snap_to_mask(mask, 55, 52, win=20)
    assert (x, y) == (60.0, 50.0)
    # nothing in range -> unchanged
    assert vx.snap_to_mask(mask, 5, 5, win=10) == (5.0, 5.0)


def test_blob_trace_makes_paths():
    pytest.importorskip("vtracer")
    sub = np.zeros((40, 60), dtype=bool)
    sub[10:30, 5:55] = True  # a solid bar
    el = vx.blob_trace(sub, (100, 200), "#ff0000")
    assert el is not None and el["kind"] == "raw"
    assert "<path" in el["svg"] and "translate(100,200)" in el["svg"] and "#ff0000" in el["svg"]


# ---- end-to-end: the tool (no network) -------------------------------------------------------
@pytest.fixture()
def figure(tmp_path):
    base = _base()
    lab = _labeled(base)
    bp, lp = tmp_path / "base.png", tmp_path / "fig.png"
    base.save(bp)
    lab.save(lp)
    return SimpleNamespace(base_path=bp, labeled_path=lp)


def test_figure_to_svg_end_to_end_raster(figure, tmp_path):
    pytest.importorskip("rapidocr")
    pytest.importorskip("vtracer")
    from figure_to_svg_tool import FigureToSvgTool

    tool = FigureToSvgTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(
        tool.execute(
            "t",
            {
                "image": str(figure.labeled_path),
                "base": str(figure.base_path),  # supply the textless base -> no strip / no network
            },
            None,
        )
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    svg = Path(r["out_svg"]).read_text(encoding="utf-8")
    assert "<image" in svg  # raster artwork embedded
    assert "<text" in svg and "Cortex" in svg  # editable label
    assert r["lines"] >= 1  # the leader/arrow traced DETERMINISTICALLY into a clean line
    assert r["out_svg"] in res.artifacts


def test_figure_to_svg_no_double_text_on_failed_strip(figure, tmp_path):
    """Regression: a wholly-FAILED strip (base == the labelled image, text still baked) must NOT
    leave the baked text under the new editable <text>. figure_to_svg must clean the base first."""
    pytest.importorskip("rapidocr")
    pytest.importorskip("vtracer")
    from figure_to_svg_tool import FigureToSvgTool

    tool = FigureToSvgTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(
        tool.execute(
            "t",
            {
                "image": str(figure.labeled_path),
                "base": str(figure.labeled_path),  # strip "failed": base still has the labels
            },
            None,
        )
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    assert r["cleaned_labels"] >= 1  # it detected + erased the left-behind label(s)
    assert r["base_png"].endswith("_base_clean.png")  # composited on the cleaned base, not the raw
    # convert to RGB: the base is now RGBA (bg removed), and transparent pixels keep white RGB
    base = np.asarray(Image.open(r["base_png"]).convert("RGB"))
    # "Cortex" was drawn ~x555-645, y118-152 on the white margin -> that box is now blank (no baked glyph)
    assert base[125:145, 560:640].min() >= 245


def test_already_converted_returns_cached_svg_no_rerun(figure, tmp_path):
    """Idempotent: if an up-to-date editable SVG already exists next to the PNG, a second call
    returns it instantly — NO strip / OCR / model calls. This is 'click the PNG again -> opens the
    converted SVG'. (We pre-place the SVG so this needs no network even though `base` is omitted.)"""
    from figure_to_svg_tool import FigureToSvgTool

    svg = figure.labeled_path.with_name(figure.labeled_path.stem + "_editable.svg")
    svg.write_text("<svg/>", encoding="utf-8")  # a newer editable SVG already exists
    tool = FigureToSvgTool(SimpleNamespace(workspace=str(tmp_path)))
    # no base, no stub — if it tried to strip/convert it would hit the network and fail; caching must short-circuit
    res = asyncio.run(tool.execute("t", {"image": str(figure.labeled_path)}, None))
    assert not res.is_error, res.content[0].text
    assert res.details["cached"] is True
    assert res.details["out_svg"] == str(svg)
    assert "Already vectorised" in res.content[0].text


def test_force_reconverts_even_if_svg_exists(figure, tmp_path, monkeypatch):
    """force:true ignores the cache and re-runs (here with semantic off + base, so no network)."""
    from figure_to_svg_tool import FigureToSvgTool

    svg = figure.labeled_path.with_name(figure.labeled_path.stem + "_editable.svg")
    svg.write_text("<svg/>", encoding="utf-8")
    tool = FigureToSvgTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(
        tool.execute(
            "t",
            {
                "image": str(figure.labeled_path),
                "base": str(figure.base_path),
                "force": True,
            },
            None,
        )
    )
    assert not res.is_error, res.content[0].text
    assert res.details["cached"] is False  # it actually re-converted


def test_split_into_multiple_selectable_objects(tmp_path):
    """The apple/mango/banana ask: 3 separate blobs -> 3 separate `<image>` objects in the SVG, each
    individually selectable (bg removed, connected-component split)."""
    pytest.importorskip("rapidocr")
    from PIL import Image as _I
    from PIL import ImageDraw as _D

    from figure_to_svg_tool import FigureToSvgTool

    im = _I.new("RGB", (600, 400), "white")
    d = _D.Draw(im)
    d.ellipse([40, 150, 160, 270], fill=(200, 60, 60))  # "apple"
    d.ellipse([250, 150, 370, 270], fill=(230, 170, 40))  # "mango"
    d.ellipse([450, 150, 560, 270], fill=(240, 220, 60))  # "banana"
    p = tmp_path / "fruits.png"
    im.save(p)
    tool = FigureToSvgTool(SimpleNamespace(workspace=str(tmp_path)))
    # base=itself (no strip/network) -> empty diff, pure artwork-splitting path
    res = asyncio.run(
        tool.execute("t", {"image": str(p), "base": str(p)}, None)
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    assert r["objects"] == 3 and r["bg_removed"] is True
    svg = Path(r["out_svg"]).read_text(encoding="utf-8")
    assert svg.count("<image") == 3  # three separate selectable image objects


def test_failed_strip_keeps_lines_as_artwork_no_double(figure, tmp_path):
    """When the strip 'fails' (base == labelled, lines intact) the tool detects it and draws NO
    vector lines (that was the double-lines bug); the lines stay baked in the raster artwork and only
    the text is editable."""
    pytest.importorskip("rapidocr")
    pytest.importorskip("vtracer")
    from figure_to_svg_tool import FigureToSvgTool

    tool = FigureToSvgTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(
        tool.execute(
            "t",
            {
                "image": str(figure.labeled_path),
                "base": str(figure.labeled_path),  # strip did nothing -> base has the lines
            },
            None,
        )
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    assert r["strip_failed"] is True
    assert r["lines"] == 0 and r["blob_shapes"] == 0  # NO vector lines -> no doubling
    assert r["labels"] >= 1  # text is still editable
    svg = Path(r["out_svg"]).read_text(encoding="utf-8")
    assert "<image" in svg and "<text" in svg  # raster artwork (lines baked in) + editable text


def test_deterministic_line_lands_on_the_ink(tmp_path):
    """DETERMINISTIC line, no LLM: a clean straight arrow (textless base supplied) is traced into ONE
    line whose endpoints land ON the actual drawn stroke — not a model's guess in a random place."""
    pytest.importorskip("rapidocr")
    from PIL import Image as _I
    from PIL import ImageDraw as _D

    from figure_to_svg_tool import FigureToSvgTool

    lab = _I.new("RGB", (500, 300), "white")
    d = _D.Draw(lab)
    d.line([(100, 150), (400, 150)], fill=(20, 20, 20), width=4)  # a horizontal shaft
    d.polygon([(400, 150), (378, 141), (378, 159)], fill=(20, 20, 20))  # arrowhead -> right
    base = _I.new("RGB", (500, 300), "white")  # textless base = the stroke removed
    lp, bp = tmp_path / "arrow.png", tmp_path / "arrow_base.png"
    lab.save(lp)
    base.save(bp)

    tool = FigureToSvgTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(tool.execute("t", {"image": str(lp), "base": str(bp)}, None))
    assert not res.is_error, res.content[0].text
    r = res.details
    assert r["lines"] >= 1  # the stroke traced into ONE clean line
    spec = json.loads(Path(r["out_json"]).read_text(encoding="utf-8"))
    lines = [e for e in spec["elements"] if e.get("kind") in ("arrow", "leader")]
    assert lines, "no line element emitted"
    xs = [p[0] for e in lines for p in e["points"]]
    ys = [p[1] for e in lines for p in e["points"]]
    # endpoints span the drawn stroke (~x100..400) at its real height (~y150) — accurate, not random
    assert min(xs) < 140 and max(xs) > 360
    assert 120 < min(ys) and max(ys) < 180
    assert "marker-end" in Path(r["out_svg"]).read_text(encoding="utf-8")  # arrowhead detected
