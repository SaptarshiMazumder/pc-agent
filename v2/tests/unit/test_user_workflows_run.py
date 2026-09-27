"""A workflow the user brings — a tutorial's editor save, foldered model names — runs as given."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime.infrastructure.net.outbound import Response
from editor_graph_converter import EditorGraphConverter
from model_download_request import ModelDownloadRequest
from model_readiness import ModelReadiness
from workflow_reference_repository import WorkflowReferenceRepository

API = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
    "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "Krea2\\krea2_identity_edit_v1_2.safetensors"}},
}


def test_a_foldered_windows_name_installs_into_its_subfolder():
    r = ModelDownloadRequest("Krea2\\lora.safetensors", "https://example.com/lora.safetensors", "lora")
    assert (r.filename, r.basename) == ("Krea2/lora.safetensors", "lora.safetensors")
    with pytest.raises(ValueError):
        ModelDownloadRequest("../lora.safetensors", "https://example.com/l.safetensors", "lora")


def test_the_run_uses_the_machine_s_own_spelling_of_a_foldered_name():
    catalogue = {"LoraLoaderModelOnly": {"input": {"required": {
        "lora_name": [["Krea2/krea2_identity_edit_v1_2.safetensors"]]}}}}
    readiness = ModelReadiness(catalogue)
    graph = {"4": API["4"]}
    assert readiness.missing(graph) == []
    assert readiness.respell(graph)["4"]["inputs"]["lora_name"] == "Krea2/krea2_identity_edit_v1_2.safetensors"


def test_the_install_list_matches_a_file_whichever_slash(monkeypatch):
    import studio_state

    monkeypatch.setattr(studio_state, "_session", lambda: "chat-1")
    monkeypatch.setattr(studio_state, "_validations",
                        lambda: {"wf": {"missing_files": ["Krea2\\krea2_identity_edit_v1_2.safetensors"]}})
    monkeypatch.setattr(studio_state, "_workflow_exists", lambda name: True)
    monkeypatch.setattr(studio_state, "checkpoint_answered", lambda name: (True, ""))
    assert studio_state.install_allowed("Krea2/krea2_identity_edit_v1_2.safetensors")[0]
    assert studio_state.install_allowed("krea2_identity_edit_v1_2.safetensors")[0]


def test_the_user_s_own_workflow_is_the_evidence_for_its_files(tmp_path):
    repo = WorkflowReferenceRepository(tmp_path, fetch=None)
    assert repo.check(API)  # nothing proves this stack yet
    repo.remember(API, "library:wf_1")
    assert repo.check(API) == []
    swapped = {**API, "3": {"class_type": "VAELoader", "inputs": {"vae_name": "other_vae.safetensors"}}}
    assert repo.check(swapped)  # a file the agent swaps in still needs its own evidence


def test_a_library_workflow_file_can_be_named_as_the_reference(tmp_path):
    lib = tmp_path / "library/uploaded/workflows/tut/v1"
    lib.mkdir(parents=True)
    (lib / "tut.json").write_text(json.dumps(API))
    fresh = WorkflowReferenceRepository(tmp_path / "other", fetch=None)
    assert fresh.check(API, "workflows/chat-1/x.json")  # the agent's own folder is not evidence
    repo = WorkflowReferenceRepository(tmp_path, fetch=None)
    assert repo.check(API, "library/uploaded/workflows/tut/v1/tut.json") == []


def _converter(object_info, converted):
    get = lambda path, **k: Response(status=200, text=json.dumps(object_info))
    post = lambda path, body, **k: converted
    return EditorGraphConverter(get=get, post=post)


UI = {"nodes": [{"id": 1, "type": "UNETLoader"}, {"id": 2, "type": "KreaPhotoToCharSheet"},
                {"id": 3, "type": "Note"}], "links": []}


def test_node_packs_the_editor_workflow_needs_are_named_first():
    assert _converter({"UNETLoader": {}}, None).missing_classes(UI) == ["KreaPhotoToCharSheet"]


def test_a_conversion_without_the_packs_is_refused_not_used():
    broken = Response(status=200, text=json.dumps({"1": {"class_type": "UNETLoader", "inputs": {}},
                                                   "2": {"inputs": {"UNKNOWN": 4}}}))
    with pytest.raises(ValueError, match="incomplete"):
        _converter({}, broken).convert(UI)


def test_a_clean_conversion_is_the_api_graph():
    good = Response(status=200, text=json.dumps(API))
    assert _converter({}, good).convert(UI) == (API, [])


def test_a_link_into_an_input_the_node_no_longer_has_is_dropped_and_named():
    graph = {"16": {"class_type": "Krea2EditModelPatch",
                    "inputs": {"model": ["18", 0], "target_latent": ["15", 0], "ref_boost": 4.0}}}
    info = {"Krea2EditModelPatch": {"input": {"required": {"model": ["MODEL"]},
                                              "optional": {"ref_boost": ["FLOAT"]}}}}
    api, dropped = _converter(info, Response(status=200, text=json.dumps(graph))).convert(UI)
    assert api["16"]["inputs"] == {"model": ["18", 0], "ref_boost": 4.0}
    assert dropped == ["node 16 (Krea2EditModelPatch).target_latent"]


def test_proven_once_stays_proven_whatever_link_is_passed_later(tmp_path):
    repo = WorkflowReferenceRepository(tmp_path, fetch=None)
    repo.remember(API, "library:wf_1")
    assert repo.check(API, "https://github.com/someone/some-repo") == []


def test_the_model_sees_what_is_still_running_as_of_now():
    import asyncio
    import time
    from agent_runtime.application.services.background_job_registry import BackgroundJobRegistry

    async def run():
        registry = BackgroundJobRegistry("chat-1", lambda *a, **k: None)
        task = asyncio.get_running_loop().create_future()
        job = registry.adopt(tool="comfy_install", args={}, tool_call_id="c1",
                             task=task, started_mono=time.monotonic())
        registry.progress(job, "krea.safetensors: GPU download downloading (42%)")
        note = registry.running_note()
        task.cancel()
        return note

    note = asyncio.run(run())
    assert "comfy_install" in note and "42%" in note


def test_the_same_file_in_another_folder_is_the_same_file():
    listed = ["qwen-image/qwen_image_vae.safetensors", "krea2_identity_edit_v1_2.safetensors"]
    assert ModelReadiness.spelled("qwen_image_vae.safetensors", listed) == listed[0]
    assert ModelReadiness.spelled("Krea2/krea2_identity_edit_v1_2.safetensors", listed) == listed[1]
    assert ModelReadiness.spelled("x.safetensors", ["a/x.safetensors", "b/x.safetensors"]) is None


def test_a_different_file_is_never_swapped_only_named_as_the_closest():
    listed = ["ltx-2.5-video-vae-bf16.safetensors"]
    assert ModelReadiness.spelled("ltx-2.5-video-vae-conv-bf16.safetensors", listed) is None
    assert ModelReadiness.closest("ltx-2.5-video-vae-conv-bf16.safetensors", listed) == listed[0]
