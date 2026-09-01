"""Tool-declared artifacts (the deliverable channel).

A PRODUCING tool declares the file(s) it made via ToolResult.artifacts; they flow through
the message + transcript untouched and render on the client. A read/search/list tool
declares nothing, so a file it merely mentions can NEVER surface — no text is scanned.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from agent_runtime.domain.messages import (
    Artifact,
    ToolResultMessage,
    message_from_dict,
    message_to_dict,
)
from agent_runtime.infrastructure.files import classify, describe_artifact, resolve_artifacts


def _png(p: Path) -> Path:
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    return p


# ---- files.py: resolve declared paths -> typed artifacts -------------------------


def test_describe_artifact_classifies_and_checks_existence(tmp_path):
    d = describe_artifact(_png(tmp_path / "final.png"))
    assert d and d["kind"] == "image" and d["mime"] == "image/png" and d["name"] == "final.png"
    assert d["size"] > 0
    assert describe_artifact(tmp_path / "missing.png") is None  # not a file -> None
    # a produced file with an unusual extension is still presentable, as a 'file'
    weird = tmp_path / "model.step"
    weird.write_text("x")
    assert describe_artifact(weird)["kind"] == "file"


def test_resolve_artifacts_dedups_and_drops_missing(tmp_path):
    a = _png(tmp_path / "a.png")
    got = resolve_artifacts([str(a), str(a), str(tmp_path / "gone.mp4"), ""])
    assert [g["name"] for g in got] == ["a.png"]  # deduped + missing dropped, order kept


def test_classify_known_kinds():
    assert classify("x.svg") == ("image", "image/svg+xml")
    assert classify("x.mp4") == ("video", "video/mp4")
    assert classify("x.pptx")[0] == "file"
    assert classify("x.unknownext") is None


# ---- domain: artifacts persist/transport with the message ------------------------


def test_toolresult_message_roundtrip_carries_artifacts(tmp_path):
    art = Artifact(
        path=str(tmp_path / "deck.pptx"),
        name="deck.pptx",
        mime="application/x",
        kind="file",
        size=3,
    )
    m = ToolResultMessage(tool_call_id="1", tool_name="make_pptx", artifacts=[art])
    d = message_to_dict(m)
    assert d["artifacts"][0]["kind"] == "file" and d["artifacts"][0]["name"] == "deck.pptx"
    back = message_from_dict(d)
    assert isinstance(back.artifacts[0], Artifact) and back.artifacts[0].name == "deck.pptx"


def test_no_declaration_means_no_artifacts_key():
    # a search/read/list tool declares nothing -> the wire form has no artifacts at all,
    # so nothing can ever be rendered from it (this is the CV-video bug, structurally gone)
    m = ToolResultMessage(tool_call_id="1", tool_name="find")
    assert m.artifacts == []
    assert "artifacts" not in message_to_dict(m)


# ---- present_files: the universal agent-declared deliverable tool ----------------


def test_show_files_declares_existing_only(tmp_path):
    from show_tool import ShowFilesTool

    tool = ShowFilesTool(SimpleNamespace(workspace=str(tmp_path)))
    _png(tmp_path / "chart.png")
    res = asyncio.run(tool.execute("c", {"files": ["chart.png", "nope.png"]}, asyncio.Event()))
    assert not res.is_error
    assert res.artifacts == [str(tmp_path / "chart.png")]  # relative resolved, missing skipped

    empty = asyncio.run(tool.execute("c", {"files": ["nope.png"]}, asyncio.Event()))
    assert empty.is_error and empty.artifacts == []


def test_zip_files_bundles_and_declares(tmp_path):
    import zipfile

    from zip_tool import ZipFilesTool

    _png(tmp_path / "a.png")
    (tmp_path / "b.svg").write_text("<svg/>")
    tool = ZipFilesTool(SimpleNamespace(workspace=str(tmp_path)))
    res = asyncio.run(
        tool.execute("c", {"files": ["a.png", "b.svg"], "out_path": "figs.zip"}, asyncio.Event())
    )
    assert not res.is_error
    zpath = res.artifacts[0]
    assert zpath.endswith("figs.zip")
    with zipfile.ZipFile(zpath) as z:
        assert sorted(z.namelist()) == ["a.png", "b.svg"]  # both bundled by basename
    # the loop would classify this declared path as a downloadable 'file' artifact
    assert describe_artifact(zpath)["kind"] == "file"
