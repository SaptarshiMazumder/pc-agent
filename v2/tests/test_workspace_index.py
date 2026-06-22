"""TURN seam: FileWorkspaceIndex surfaces the agent's workspace files (scripts/docs/
images/data) as a manifest, so resources it created are always visible + reusable.
Bounded + noise-filtered so it's safe even on a big directory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.workspace import FileWorkspaceIndex, build_workspace_index
from types import SimpleNamespace


def _ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def test_manifest_lists_and_classifies(tmp_path):
    ws = _ws(tmp_path)
    (ws / "generate_sheet.py").write_text("# generates the monthly cost sheet\nprint(1)\n", encoding="utf-8")
    (ws / "NOTES.md").write_text("# Cost tracking\nbody\n", encoding="utf-8")
    (ws / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (ws / "costs.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    m = FileWorkspaceIndex().manifest(ws)
    assert "## Your workspace resources" in m
    assert "**scripts**" in m and "**docs**" in m and "**images**" in m and "**data**" in m
    assert "generate_sheet.py" in m and "generates the monthly cost sheet" in m
    assert "NOTES.md" in m and "chart.png" in m and "costs.csv" in m


def test_empty_workspace_no_manifest(tmp_path):
    assert FileWorkspaceIndex().manifest(_ws(tmp_path)) == ""


def test_missing_workspace_no_manifest(tmp_path):
    assert FileWorkspaceIndex().manifest(tmp_path / "does-not-exist") == ""


def test_skips_noise_dirs_and_files(tmp_path):
    ws = _ws(tmp_path)
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "x.pyc").write_bytes(b"x")
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("x", encoding="utf-8")
    (ws / "real.py").write_text("print(1)", encoding="utf-8")

    paths = [r.rel_path for r in FileWorkspaceIndex().scan(ws)]
    assert "real.py" in paths
    assert not any("__pycache__" in p or ".git" in p for p in paths)


def test_classifies_unknown_as_other(tmp_path):
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("x", encoding="utf-8")
    (ws / "b.weirdext").write_text("x", encoding="utf-8")
    kinds = {r.rel_path: r.kind for r in FileWorkspaceIndex().scan(ws)}
    assert kinds["a.py"] == "script" and kinds["b.weirdext"] == "other"


def test_max_files_cap(tmp_path):
    ws = _ws(tmp_path)
    for i in range(10):
        (ws / f"f{i}.txt").write_text("x", encoding="utf-8")
    idx = FileWorkspaceIndex(max_files=3)
    assert len(idx.scan(ws)) == 3
    assert "showing the first 3" in idx.manifest(ws)


def test_build_workspace_index_gated_by_flag():
    assert build_workspace_index(SimpleNamespace(workspace_index_enabled=False)) is None
    idx = build_workspace_index(SimpleNamespace(workspace_index_enabled=True, workspace_index_max_files=50))
    assert isinstance(idx, FileWorkspaceIndex)
