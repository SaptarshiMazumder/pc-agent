"""Regression coverage for numeric links observed in the live Qwen E2E run."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest

import comfy_bridge
import comfy_emit
import studio_state
from agent_runtime.infrastructure.net.outbound import Response
from workflow_link import WorkflowLink


def graph(link):
    return {
        "9": {"class_type": "EmptyImage", "inputs": {"width": 1024}},
        "10": {"class_type": "SaveImage", "inputs": {
            "images": link, "filename_prefix": "qwen_stills",
        }},
    }


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    for module in (comfy_emit, comfy_bridge):
        monkeypatch.setattr(module, "current_workspace", lambda *args: str(tmp_path))
    for method in ("mark_emitted", "mark_first_emit", "mark_validated", "forget_validated"):
        monkeypatch.setattr(studio_state, method, Mock())
    monkeypatch.setattr(studio_state, "uploaded_in_session", lambda: [])
    monkeypatch.setattr(studio_state, "downloaded_in_session", lambda: [])
    monkeypatch.setattr(comfy_bridge, "_settle_downloads", AsyncMock(return_value=""))
    catalogue = {
        "EmptyImage": {"input": {"required": {"width": ["INT"]}}, "output": ["IMAGE"]},
        "SaveImage": {"input": {"required": {
            "images": ["IMAGE"], "filename_prefix": ["STRING"],
        }}},
    }
    monkeypatch.setattr(comfy_bridge, "_get", Mock(return_value=Response(
        status=200, text=json.dumps(catalogue),
    )))
    return tmp_path


async def emit(link):
    nodes = [{"id": key, **node} for key, node in graph(link).items()]
    original = copy.deepcopy(nodes)
    result = await comfy_emit.ComfyEmitTool().execute(
        "test", {"name": "stills", "nodes": nodes}, asyncio.Event(),
    )
    assert nodes == original  # serialization must not rewrite the caller's tool arguments
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("link", [[9, 0], ["9", 0]])
async def test_emit_writes_canonical_links_in_api_and_all_ui_wire_tables(isolated_workspace, link):
    result = await emit(link)
    assert not result.is_error, result.content[0].text
    api = json.loads((isolated_workspace / result.details["api"]).read_text())
    ui = json.loads((isolated_workspace / result.details["ui"]).read_text())
    saved_link = api["10"]["inputs"]["images"]
    assert saved_link == ["9", 0]
    # Match the GPU's direct lookup, with NO str() cast hiding a broken link.
    assert api[saved_link[0]]["class_type"] == "EmptyImage"
    assert ui["links"] == [[1, "9", 0, "10", 0, ""]]
    by_id = {node["id"]: node for node in ui["nodes"]}
    assert by_id["10"]["inputs"][0]["link"] == 1
    assert by_id["9"]["outputs"][0]["links"] == [1]
    assert by_id["10"]["widgets_values"] == ["qwen_stills"]
    checked = await comfy_bridge.ComfyValidateTool().execute("test", {
        "workflow_path": str(isolated_workspace / result.details["api"]),
    }, asyncio.Event())
    assert not checked.is_error, checked.content[0].text


BAD_LINKS = [
    ["missing", 0], [999, 0], [True, 0], [9.0, 0], [None, 0], ["", 0],
    ["9", -1], ["9", True], ["9", 0.5], ["9", "0"],
    [], ["9"], ["9", 0, 1], [["9", 0], ["9", 0]],
]


@pytest.mark.asyncio
@pytest.mark.parametrize("link", BAD_LINKS)
async def test_emit_refuses_bad_links_before_writing_files(isolated_workspace, link):
    result = await emit(link)
    assert result.is_error
    assert "node 10.images" in result.content[0].text
    assert not list(isolated_workspace.rglob("*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("link", [[9, 0], *BAD_LINKS])
async def test_validate_rejects_actual_broken_file_without_silently_fixing_it(isolated_workspace, link):
    path = isolated_workspace / "stills.api.json"
    original = json.dumps(graph(link))
    path.write_text(original)
    result = await comfy_bridge.ComfyValidateTool().execute(
        "test", {"workflow_path": str(path)}, asyncio.Event(),
    )
    assert result.is_error
    assert "node 10.images" in result.content[0].text
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_wrapped_literal_array_is_not_mistaken_for_a_connection(isolated_workspace):
    literal = {"__value__": [9, 0]}
    result = await emit(literal)
    assert not result.is_error
    api = json.loads((isolated_workspace / result.details["api"]).read_text())
    ui = json.loads((isolated_workspace / result.details["ui"]).read_text())
    assert api["10"]["inputs"]["images"] == literal
    assert ui["links"] == []
    assert ui["nodes"][1]["widgets_values"] == [literal, "qwen_stills"]


@pytest.mark.parametrize("value", [0, 0.7, True, None, "@model", "qwen.safetensors", {"__value__": [9, 0]}])
def test_link_parser_leaves_literals_alone(value):
    assert WorkflowLink.from_input(value, {"9"}, normalize_node_id=True) is None


@pytest.mark.parametrize("node_id", [0, "0", "decoder", "group:9"])
def test_link_parser_preserves_zero_named_ids_and_nonzero_output_slots(node_id):
    value = [node_id, 2]
    link = WorkflowLink.from_input(value, {str(node_id)}, normalize_node_id=True)
    assert link.as_input() == [str(node_id), 2]
    assert value == [node_id, 2]
    assert WorkflowLink.from_input(link.as_input(), {str(node_id)}) == link
