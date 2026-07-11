"""figure_to_svg — the one-call labelled-figure -> editable SVG converter, and its new helpers
(semantic arrow reader, snap-to-mask, blob-trace). Deterministic: the end-to-end runs with a
supplied textless base (no image-model strip) and semantic_arrows off (no VLM), so no network.
The semantic path is exercised separately with a stubbed VLM.
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
                "base": str(figure.base_path),  # supply base -> no strip / no network
                "semantic_arrows": False,  # blob-trace only -> no VLM / no network
            },
            None,
        )
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    svg = Path(r["out_svg"]).read_text(encoding="utf-8")
    assert "<image" in svg  # raster artwork embedded
    assert "<text" in svg and "Cortex" in svg  # editable label
    assert r["blob_shapes"] >= 1  # the leader/arrow traced as vector shapes
    assert r["semantic_arrows"] == 0  # semantic disabled
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
                "semantic_arrows": False,
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
                "semantic_arrows": False,
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
    # base=itself (no strip/network), no semantic arrows -> pure artwork-splitting path
    res = asyncio.run(
        tool.execute("t", {"image": str(p), "base": str(p), "semantic_arrows": False}, None)
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    assert r["objects"] == 3 and r["bg_removed"] is True
    svg = Path(r["out_svg"]).read_text(encoding="utf-8")
    assert svg.count("<image") == 3  # three separate selectable image objects


def test_failed_strip_keeps_lines_as_artwork_no_double(figure, tmp_path):
    """The lungs regression: when the strip 'fails' (base == labelled, lines intact), the tool must
    detect it and NOT draw vector lines on top of the baked ones (that was the double-lines bug).
    Lines stay as artwork; only text is editable."""
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
                "semantic_arrows": True,  # even with it on, no vectors should be drawn on a failed strip
            },
            None,
        )
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    assert r["strip_failed"] is True
    assert r["semantic_arrows"] == 0 and r["blob_shapes"] == 0  # NO vector lines -> no doubling
    assert r["labels"] >= 1  # text is still editable
    svg = Path(r["out_svg"]).read_text(encoding="utf-8")
    assert "<image" in svg and "<text" in svg  # raster artwork (lines baked in) + editable text


def test_figure_to_svg_semantic_arrows_stubbed(figure, tmp_path, monkeypatch):
    """Exercise the semantic path deterministically: stub the VLM so no network is used, and assert
    an arrow element is built with its centre-line snapped onto the drawn stroke."""
    pytest.importorskip("rapidocr")
    from figure_to_svg_tool import FigureToSvgTool

    # the labelled figure's curved arrow runs ~ (620,470)->(447,330); point the stub at it (normalized)
    def _stub_vlm(self, image_path, api_key):
        raw = json.dumps(
            [
                {
                    "kind": "arrow",
                    "from": [int(470 / H * 1000), int(620 / W * 1000)],
                    "to": [int(330 / H * 1000), int(447 / W * 1000)],
                    "curved": True,
                    "color": "#333333",
                }
            ]
        )
        return (lambda _p: raw, json.loads)

    monkeypatch.setattr(FigureToSvgTool, "_vlm", _stub_vlm)
    tool = FigureToSvgTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(
        tool.execute(
            "t",
            {
                "image": str(figure.labeled_path),
                "base": str(figure.base_path),
                "semantic_arrows": True,
            },
            None,
        )
    )
    assert not res.is_error, res.content[0].text
    r = res.details
    assert r["semantic_arrows"] >= 1
    # a real arrowhead was drawn (semantic arrow with a marker), not a blob fill
    svg = Path(r["out_svg"]).read_text(encoding="utf-8")
    assert "marker-end" in svg
