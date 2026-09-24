"""The Library, from the agent's side: the catalogue is read, a workflow is summarised and
brought into THIS chat as its own workflow (slots recorded), a reference becomes a copy
instruction for the window, a file is read as text — and an empty or broken Library says so."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import library_find_tool
import library_read_tool
import library_use_tool
import studio_state
from agent_runtime.application import run_context as rc
from agent_runtime.application.run_context import RunContext
from library_index import LibraryIndex
from workflow_summary import WorkflowSummary

GRAPH = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "@model"}, "_meta": {"title": "face"}},
    "2": {"class_type": "LoadImage", "inputs": {"image": "@garment"}},
    "10": {
        "class_type": "ByteDance2TextToVideoNode",
        "inputs": {
            "model": "seedance-2-5",
            "prompt": "p" * 400,
            "image_1": ["1", 0],
            "image_2": ["2", 0],
            "duration": 8,
        },
    },
    "20": {"class_type": "SaveVideo", "inputs": {"video": ["10", 0], "format": "mp4"}},
}

INDEX = {
    "version": 1,
    "items": [
        {
            "id": "wf_1", "kind": "workflow", "origin": "saved", "name": "jacket-reel",
            "note": "the reel", "path": "saved/workflows/jacket-reel",
            "versions": [{"v": 1, "slots": ["model", "garment"]}, {"v": 2, "slots": ["model", "garment"]}],
            "from": {"chat": "chat-0", "title": "AI Influencer Jacket"},
        },
        {
            "id": "ref_1", "kind": "reference", "origin": "uploaded", "name": "face",
            "path": "uploaded/references/face.png",
        },
        {
            "id": "file_1", "kind": "file", "origin": "uploaded", "name": "notes",
            "path": "uploaded/files/notes.txt",
        },
        {
            "id": "file_2", "kind": "file", "origin": "uploaded", "name": "pasted-graph",
            "path": "uploaded/files/video.json",
        },
    ],
}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    lib = tmp_path / "library"
    (lib / "saved" / "workflows" / "jacket-reel" / "v1").mkdir(parents=True)
    (lib / "saved" / "workflows" / "jacket-reel" / "v2").mkdir(parents=True)
    (lib / "saved" / "workflows" / "jacket-reel" / "v1" / "jacket-reel.api.json").write_text(
        json.dumps({"1": {"class_type": "Old", "inputs": {}}}), encoding="utf-8"
    )
    (lib / "saved" / "workflows" / "jacket-reel" / "v2" / "jacket-reel.api.json").write_text(
        json.dumps(GRAPH), encoding="utf-8"
    )
    (lib / "saved" / "workflows" / "jacket-reel" / "v2" / "jacket-reel.json").write_text(
        json.dumps({"nodes": [{"id": 1, "type": "LoadImage", "widgets_values": ["x"]}], "links": []}),
        encoding="utf-8",
    )
    (lib / "uploaded" / "references").mkdir(parents=True)
    (lib / "uploaded" / "references" / "face.png").write_bytes(b"\x89PNG fake")
    (lib / "uploaded" / "files").mkdir(parents=True)
    (lib / "uploaded" / "files" / "notes.txt").write_text("shoot on a street\n", encoding="utf-8")
    (lib / "uploaded" / "files" / "video.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    (lib / "index.json").write_text(json.dumps(INDEX), encoding="utf-8")
    tok = rc._current.set(RunContext("comfy-artchitect", "chat-1", "interactive", workspace=str(tmp_path)))
    for m in ("mark_emitted", "mark_first_emit"):
        monkeypatch.setattr(studio_state, m, Mock())
    yield tmp_path
    rc._current.reset(tok)


def run(tool, **params):
    """The result with its text flattened: `ToolResult.text` is the constructor, not the words."""
    r = asyncio.run(tool.execute("t", params, None))
    return SimpleNamespace(
        text=" ".join(getattr(b, "text", "") for b in r.content),
        is_error=r.is_error,
        details=r.details,
    )


# ---------------------------------------------------------------- the catalogue

def test_index_lists_only_well_formed_items(ws):
    idx = LibraryIndex.load(ws)
    assert [i.id for i in idx.items] == ["wf_1", "ref_1", "file_1", "file_2"]
    assert idx.items[0].latest_version == 2


def test_index_resolves_by_id_or_unique_name(ws):
    idx = LibraryIndex.load(ws)
    assert idx.resolve("wf_1")[0].name == "jacket-reel"
    assert idx.resolve("Jacket-Reel")[0].id == "wf_1"
    item, why = idx.resolve("nothing")
    assert item is None and "library_find" in why


def test_missing_index_is_an_empty_library(tmp_path):
    idx = LibraryIndex.load(tmp_path)
    assert idx.items == [] and idx.problem == ""


def test_broken_index_is_reported_not_swallowed(tmp_path):
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "index.json").write_text("{not json", encoding="utf-8")
    assert "could not be read" in LibraryIndex.load(tmp_path).problem


# ---------------------------------------------------------------- the summary

def test_summary_reads_an_api_graph_out_loud():
    s = WorkflowSummary(GRAPH)
    assert s.format == "api"
    text = s.text()
    assert "4 nodes, 3 links" in text
    assert "slots: @garment, @model" in text
    assert "models named: seedance-2-5" in text
    assert "image_1 ← #1" in text
    assert "(400 chars)" in text  # the prompt is cut, its length named


def test_summary_tells_editor_format_apart():
    s = WorkflowSummary({"nodes": [{"id": 3, "type": "KSampler", "widgets_values": [7]}], "links": []})
    assert s.format == "ui"
    assert "EDITOR-format" in s.text() and "#3 KSampler" in s.text()


# ---------------------------------------------------------------- find

def test_find_lists_and_filters(ws):
    r = run(library_find_tool.LibraryFindTool())
    assert "4 Library item(s)" in r.text and "(from: AI Influencer Jacket)" in r.text
    r = run(library_find_tool.LibraryFindTool(), kind="reference")
    assert "1 Library item(s)" in r.text and "face" in r.text
    r = run(library_find_tool.LibraryFindTool(), query="reel")
    assert "wf_1" in r.text and "ref_1" not in r.text


def test_find_on_an_empty_library_says_so(tmp_path):
    tok = rc._current.set(RunContext("comfy-artchitect", "c", "interactive", workspace=str(tmp_path)))
    try:
        r = run(library_find_tool.LibraryFindTool())
    finally:
        rc._current.reset(tok)
    assert "The Library is empty" in r.text and not r.is_error


# ---------------------------------------------------------------- read

def test_read_workflow_gives_summary_and_raw_of_the_latest_version(ws):
    r = run(library_read_tool.LibraryReadTool(), item="jacket-reel")
    assert not r.is_error
    assert "jacket-reel v2" in r.text
    assert "slots: @garment, @model" in r.text
    assert '"seedance-2-5"' in r.text  # the raw graph travels
    assert "library_use(item='wf_1')" in r.text


def test_read_workflow_picks_a_named_version(ws):
    r = run(library_read_tool.LibraryReadTool(), item="wf_1", version=1)
    assert "jacket-reel v1" in r.text and "Old" in r.text


def test_read_file_is_its_text(ws):
    r = run(library_read_tool.LibraryReadTool(), item="notes")
    assert "shoot on a street" in r.text


def test_read_reference_never_opens_the_pixels(ws):
    r = run(library_read_tool.LibraryReadTool(), item="face")
    assert "face.png" in r.text and "bytes" in r.text and "as='<role>'" in r.text
    assert "PNG" not in r.text


def test_read_a_pasted_graph_labelled_as_a_file_reads_as_a_workflow(ws):
    r = run(library_read_tool.LibraryReadTool(), item="pasted-graph")
    assert "is a ComfyUI workflow (api format)" in r.text and "slots: @garment, @model" in r.text


def test_read_unknown_item_points_at_find(ws):
    r = run(library_read_tool.LibraryReadTool(), item="zzz")
    assert r.is_error and "library_find" in r.text


# ---------------------------------------------------------------- use

def test_use_workflow_copies_it_into_this_chat_and_records_its_slots(ws):
    r = run(library_use_tool.LibraryUseTool(), item="wf_1", **{"as": "video"})
    assert not r.is_error, r.text
    assert (ws / "workflows" / "chat-1" / "video.api.json").exists()
    assert (ws / "workflows" / "chat-1" / "video.json").exists()
    assert "run this:    workflows/chat-1/video.api.json" in r.text
    assert "@model" in r.text and "@garment" in r.text and "EMPTY" in r.text
    slots = json.loads((ws / "references" / "chat-1" / ".slots.json").read_text(encoding="utf-8"))
    assert slots["model"]["workflows"] == ["video"]
    studio_state.mark_emitted.assert_called_once()
    studio_state.mark_first_emit.assert_called_once_with("video")


def test_use_workflow_defaults_to_the_library_name(ws):
    r = run(library_use_tool.LibraryUseTool(), item="jacket-reel")
    assert (ws / "workflows" / "chat-1" / "jacket-reel.api.json").exists()
    assert r.details["workflow"] == "jacket-reel"


def test_use_reference_is_an_instruction_for_the_window(ws):
    r = run(library_use_tool.LibraryUseTool(), item="face", **{"as": "@model"})
    assert not r.is_error
    assert r.details["copy"] == [
        {"from": "library/uploaded/references/face.png", "to": "references/chat-1/model.png", "role": "model"}
    ]
    assert not (ws / "references" / "chat-1" / "model.png").exists()  # the window copies, not the tool


def test_use_reference_without_a_role_refuses(ws):
    r = run(library_use_tool.LibraryUseTool(), item="face")
    assert r.is_error and "as='model'" in r.text


def test_use_pasted_graph_file_is_a_workflow(ws):
    r = run(library_use_tool.LibraryUseTool(), item="pasted-graph", **{"as": "reel"})
    assert not r.is_error
    assert (ws / "workflows" / "chat-1" / "reel.api.json").exists()
    assert "no editor json" in r.text


def test_use_plain_file_has_nothing_to_bring(ws):
    r = run(library_use_tool.LibraryUseTool(), item="notes")
    assert not r.is_error and "library_read" in r.text
    assert not list((ws / "workflows").glob("**/*")) if (ws / "workflows").exists() else True


def test_use_missing_files_is_reported(ws):
    (ws / "library" / "uploaded" / "references" / "face.png").unlink()
    r = run(library_use_tool.LibraryUseTool(), item="face", **{"as": "model"})
    assert r.is_error and "not on disk" in r.text


def test_use_reference_must_name_a_declared_slot_once_any_exist(ws):
    # Nothing declared yet: the role is taken as given (the ask may name it before any graph).
    r = run(library_use_tool.LibraryUseTool(), item="face", **{"as": "face"})
    assert not r.is_error
    # A workflow declares @model and @garment: a reference now fills one of THOSE, not a name.
    run(library_use_tool.LibraryUseTool(), item="wf_1", **{"as": "video"})
    r = run(library_use_tool.LibraryUseTool(), item="face", **{"as": "face"})
    assert r.is_error and "@garment, @model" in r.text and "as='garment'" in r.text
    r = run(library_use_tool.LibraryUseTool(), item="face", **{"as": "model"})
    assert not r.is_error and r.details["copy"][0]["to"] == "references/chat-1/model.png"
