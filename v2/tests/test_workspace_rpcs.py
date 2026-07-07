"""workspace.* RPCs — the entity-page file browser: lazy listing, mkdir, upload, delete,
containment guards (never escape the root, never delete the root)."""

import base64
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.memory import projects_store


def _gateway(tmp_path):
    from agentd.presentation.gateway import Gateway

    ws = tmp_path / "agents" / "main" / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    gw = Gateway(
        config=SimpleNamespace(state_dir=tmp_path, workspace=ws),
        service=None,
        registry=SimpleNamespace(
            list_ids=lambda: ["main"],
            get=lambda a: {"main": SimpleNamespace(workspace=ws, state_dir=tmp_path / "agents" / "main")}[a],
        ),
    )
    return gw, ws


def test_list_dirs_first_and_kinds(tmp_path):
    gw, ws = _gateway(tmp_path)
    (ws / "zeta.png").write_bytes(b"png")
    (ws / "alpha.txt").write_text("hi", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "inner.mp4").write_bytes(b"vid")

    out = gw._workspace_list({"agentId": "main"})
    names = [(e["name"], e["kind"]) for e in out["entries"]]
    assert names == [("sub", "folder"), ("alpha.txt", "file"), ("zeta.png", "image")]

    inner = gw._workspace_list({"agentId": "main", "path": "sub"})
    assert [(e["name"], e["kind"]) for e in inner["entries"]] == [("inner.mp4", "video")]
    assert inner["entries"][0]["rel"] == "sub/inner.mp4"


def test_mkdir_upload_delete_roundtrip(tmp_path):
    gw, ws = _gateway(tmp_path)

    assert gw._workspace_mkdir({"agentId": "main", "path": "docs/drafts"})["ok"]
    assert (ws / "docs" / "drafts").is_dir()

    b64 = base64.b64encode(b"hello").decode()
    up = gw._workspace_upload({"agentId": "main", "path": "docs", "name": "note.txt", "dataBase64": b64})
    assert up["ok"] and (ws / "docs" / "note.txt").read_bytes() == b"hello"
    # collision -> deduped name, never overwrites
    up2 = gw._workspace_upload({"agentId": "main", "path": "docs", "name": "note.txt", "dataBase64": b64})
    assert up2["ok"] and up2["name"] == "note (2).txt"

    assert gw._workspace_delete({"agentId": "main", "path": "docs/note.txt"})["ok"]
    assert not (ws / "docs" / "note.txt").exists()
    assert gw._workspace_delete({"agentId": "main", "path": "docs"})["ok"]   # recursive
    assert not (ws / "docs").exists()


def test_guards(tmp_path):
    gw, ws = _gateway(tmp_path)
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")

    # traversal is contained
    out = gw._workspace_list({"agentId": "main", "path": "../.."})
    assert out["entries"] == [] and out.get("error")
    assert not gw._workspace_delete({"agentId": "main", "path": "../secret.txt"})["ok"]
    # the root itself can't be deleted
    assert not gw._workspace_delete({"agentId": "main", "path": ""})["ok"]
    # unknown entities are clean errors
    assert gw._workspace_list({"agentId": "ghost"})["error"].startswith("unknown agent")
    assert gw._workspace_list({"projectId": "proj-nope"})["error"] == "unknown project"


def test_project_workspace_scope(tmp_path):
    gw, _ = _gateway(tmp_path)
    p = projects_store.create_project(tmp_path, "Q3")

    assert gw._workspace_mkdir({"projectId": p["id"], "path": "assets"})["ok"]
    out = gw._workspace_list({"projectId": p["id"]})
    assert [e["name"] for e in out["entries"]] == ["assets"]
    # lives under <state_dir>/projects/<id>/workspace — the §11 shared folder
    assert (tmp_path / "projects" / p["id"] / "workspace" / "assets").is_dir()
