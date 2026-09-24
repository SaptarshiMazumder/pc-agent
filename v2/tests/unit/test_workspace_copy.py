"""workspace.copy and upload's `overwrite`: the two daemon-side moves the Library needs — a
render into the Library, a Library reference into a chat's slot, the index rewritten in place —
fenced like every other workspace op."""

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime.presentation.gateway import Gateway


@pytest.fixture
def gw(tmp_path):
    g = Gateway.__new__(Gateway)  # no daemon: only the workspace ops are exercised
    g.registry = None
    g.config = SimpleNamespace(workspace=tmp_path, state_dir=tmp_path)
    (tmp_path / "outputs" / "chat-a").mkdir(parents=True)
    (tmp_path / "outputs" / "chat-a" / "render.png").write_bytes(b"png")
    (tmp_path / "outputs" / "chat-a" / "sub").mkdir()
    (tmp_path / "outputs" / "chat-a" / "sub" / "x.txt").write_text("x", encoding="utf-8")
    return g


def test_copy_a_file_into_a_new_folder(gw, tmp_path):
    r = gw._workspace_copy({"from": "outputs/chat-a/render.png", "to": "library/saved/references/render.png"})
    assert r["ok"], r
    assert (tmp_path / "library" / "saved" / "references" / "render.png").read_bytes() == b"png"
    assert (tmp_path / "outputs" / "chat-a" / "render.png").exists()  # a copy, not a move


def test_copy_a_folder_recursively(gw, tmp_path):
    r = gw._workspace_copy({"from": "outputs/chat-a", "to": "library/saved/workflows/w/v1"})
    assert r["ok"], r
    assert (tmp_path / "library" / "saved" / "workflows" / "w" / "v1" / "sub" / "x.txt").exists()


def test_copy_never_overwrites_unless_asked(gw, tmp_path):
    (tmp_path / "references" / "chat-b").mkdir(parents=True)
    (tmp_path / "references" / "chat-b" / "model.png").write_bytes(b"old")
    r = gw._workspace_copy({"from": "outputs/chat-a/render.png", "to": "references/chat-b/model.png"})
    assert not r["ok"] and r["error"] == "exists"
    assert (tmp_path / "references" / "chat-b" / "model.png").read_bytes() == b"old"
    r = gw._workspace_copy({"from": "outputs/chat-a/render.png", "to": "references/chat-b/model.png", "overwrite": True})
    assert r["ok"]
    assert (tmp_path / "references" / "chat-b" / "model.png").read_bytes() == b"png"


@pytest.mark.parametrize("params", [
    {"from": "", "to": "x"},
    {"from": "outputs/chat-a/render.png", "to": ""},
    {"from": "../secret", "to": "x"},
    {"from": "outputs/chat-a/render.png", "to": "../out"},
    {"from": "outputs/chat-a/render.png", "to": "."},
])
def test_copy_refuses_escapes_and_the_root(gw, params):
    r = gw._workspace_copy(params)
    assert not r["ok"]


def test_copy_missing_source(gw):
    r = gw._workspace_copy({"from": "outputs/chat-a/nope.png", "to": "library/x.png"})
    assert not r["ok"] and r["error"] == "not found"


def test_upload_dedupes_by_default_and_overwrites_when_asked(gw, tmp_path):
    b64 = base64.b64encode(b'{"v":1}').decode("ascii")
    r1 = gw._workspace_upload({"path": "library", "name": "index.json", "dataBase64": b64})
    r2 = gw._workspace_upload({"path": "library", "name": "index.json", "dataBase64": b64})
    assert r1["ok"] and r2["ok"] and r2["name"] == "index (2).json"
    b64 = base64.b64encode(b'{"v":2}').decode("ascii")
    r3 = gw._workspace_upload({"path": "library", "name": "index.json", "dataBase64": b64, "overwrite": True})
    assert r3["ok"] and r3["name"] == "index.json"
    assert (tmp_path / "library" / "index.json").read_text(encoding="utf-8") == '{"v":2}'
