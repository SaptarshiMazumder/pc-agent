"""compose_figure_layers overlay-input hardening.

Regression for the bug where a FILENAME passed into the raw-markup `overlay_svg` field was
embedded verbatim, producing a figure with the base artwork + a stray text node and NO labels.
The tool must auto-recover a real file, pass real markup through, and reject anything else.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compose_layers_tool import ComposeLayersTool  # plugins/figures is on sys.path via conftest

OVERLAY = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
    '<text x="10" y="20">Fan Blades</text></svg>'
)


def _tool(tmp_path):
    return ComposeLayersTool(SimpleNamespace(workspace=str(tmp_path)))


def test_real_markup_passes_through(tmp_path):
    assert _tool(tmp_path)._overlay_text({"overlay_svg": OVERLAY}) == OVERLAY


def test_filename_in_markup_field_is_recovered(tmp_path):
    # THE bug: a filename went into overlay_svg (the markup field). Load it instead of embedding it.
    (tmp_path / "ov.svg").write_text(OVERLAY, encoding="utf-8")
    assert "Fan Blades" in _tool(tmp_path)._overlay_text({"overlay_svg": "ov.svg"})


def test_non_svg_non_file_is_rejected(tmp_path):
    with pytest.raises(ValueError) as e:
        _tool(tmp_path)._overlay_text({"overlay_svg": "ov.svg"})  # not markup, no such file
    assert "overlay_svg_path" in str(e.value)


def test_path_field_still_reads_the_file(tmp_path):
    (tmp_path / "ov.svg").write_text(OVERLAY, encoding="utf-8")
    assert "Fan Blades" in _tool(tmp_path)._overlay_text({"overlay_svg_path": "ov.svg"})


def test_composed_svg_embeds_overlay_not_filename(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (64, 64), "white").save(tmp_path / "art.png")
    (tmp_path / "ov.svg").write_text(OVERLAY, encoding="utf-8")
    _tool(tmp_path)._run({"artwork": "art.png", "overlay_svg": "ov.svg", "out_svg": "final.svg"})
    out = (tmp_path / "final.svg").read_text(encoding="utf-8")
    assert "Fan Blades" in out and "<text" in out  # labels really embedded now
    assert "ov.svg</svg>" not in out  # NOT the old stray-filename node
