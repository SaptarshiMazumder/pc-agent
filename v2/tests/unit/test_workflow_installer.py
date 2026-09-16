"""Portable exports never execute during validation or inherit platform credentials."""

import asyncio
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import urllib.error
from unittest.mock import AsyncMock, Mock

import pytest

import comfy_bridge
from agent_runtime.infrastructure.net.outbound import Response
from installer_source_policy import InstallerSourcePolicy
from workflow_dependency_repository import WorkflowDependencyRepository
from workflow_installer_exporter import WorkflowInstallerExporter
from workflow_installer_manifest import WorkflowInstallerManifest
from workflow_installer_runtime import WorkflowInstallerRuntime


URL = "https://huggingface.co/org/repo/resolve/main/model.safetensors"
GRAPH = {"1": {"class_type": "VAELoader", "inputs": {"vae_name": "model.safetensors"}}}
CATALOGUE = {"VAELoader": {"python_module": "nodes", "input": {
    "required": {"vae_name": [["model.safetensors"]]}}}}


def model():
    return {"url": URL, "destination": "models/vae/model.safetensors"}


def recorded():
    return [{**model(), "filename": "model.safetensors", "kind": "vae", "evidence": "comfy_install"}]


def manifest():
    return WorkflowInstallerManifest().build(GRAPH, CATALOGUE, recorded(), [], {})


def tensor():
    header = json.dumps({"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    return struct.pack("<Q", len(header)) + header + bytes(4)


class DownloadResponse(io.BytesIO):
    def __init__(self, content=None, headers=None):
        content = tensor() if content is None else content
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content))} if headers is None else headers


@pytest.fixture
def comfy_root(tmp_path):
    (tmp_path / "main.py").touch()
    (tmp_path / "models").mkdir()
    return tmp_path


@pytest.mark.parametrize("path", ["../a", "models/../a", "C:/a", "/tmp/a", "models//a", "models/NUL", "models/a."])
def test_paths_cannot_escape_or_use_windows_special_names(path):
    with pytest.raises(ValueError):
        InstallerSourcePolicy.relative_path(path)


@pytest.mark.parametrize("url", [
    "http://example.com/model", "https://user:secret@example.com/model",
    "https://example.com/model?token=secret", "https://cdn.example.com/x?X-Amz-Signature=secret",
    "https://huggingface.co/org/repo/blob/main/x", "https://civitai.com/models/123",
    "https://example.com/${SECRET}",
    "https://localhost/model", "https://127.0.0.1/model", "https://169.254.169.254/model",
])
def test_export_refuses_secrets_signed_links_and_non_download_provider_urls(url):
    with pytest.raises(ValueError):
        InstallerSourcePolicy.source_url(url)


def test_provider_source_removes_credentials_preserves_file_identity():
    assert InstallerSourcePolicy.source_url(
        "https://civitai.com/api/download/models/2532617?fileId=2420425&token=SECRET"
    ) == "https://civitai.com/api/download/models/2532617?fileId=2420425"
    assert InstallerSourcePolicy.source_url(URL + "?token=SECRET") == URL


def test_repository_saves_only_permanent_source(tmp_path):
    repository = WorkflowDependencyRepository(tmp_path)
    repository.record_model({"url": URL + "?token=SECRET", "kind": "vae"}, "sub/model.safetensors")
    record = repository.models()[0]
    assert record["destination"] == "models/vae/sub/model.safetensors"
    assert record["url"] == URL
    assert "SECRET" not in next(repository.root.glob("*.json")).read_text()


def test_manifest_includes_only_models_used_by_graph():
    entries = recorded() + [{**recorded()[0], "filename": "unrelated.safetensors"}]
    result = WorkflowInstallerManifest().build(GRAPH, CATALOGUE, entries, [], {})
    assert result["unresolved"] == []
    assert len(result["models"]) == 1
    assert result["workflow_sha256"] == WorkflowInstallerManifest.fingerprint(GRAPH)


def test_preinstalled_models_resolve_from_manager_and_unknown_sources_are_explicit():
    result = WorkflowInstallerManifest().build(GRAPH, CATALOGUE, [], [
        {"filename": "model.safetensors", "url": URL, "type": "vae"},
    ], {})
    assert not result["unresolved"]
    assert result["models"][0]["destination"] == "models/vae/model.safetensors"
    missing = WorkflowInstallerManifest().build(GRAPH, CATALOGUE, [], [], {})
    assert missing["unresolved"] and not missing["models"]


def test_manifest_tracks_custom_nodes_paid_apis_and_reference_slots():
    graph = {"1": {"class_type": "Custom", "inputs": {"image": "@identity"}},
             "2": {"class_type": "Paid", "inputs": {}}}
    catalogue = {"Custom": {"python_module": "custom_nodes.SomePack.nodes"},
                 "Paid": {"python_module": "comfy_api_nodes.foo", "api_node": True}}
    result = WorkflowInstallerManifest().build(graph, catalogue, [], [], {
        "some-id": {"repository": "https://github.com/org/SomePack"}})
    assert not result["unresolved"]
    assert result["node_packs"][0]["repository"] == "https://github.com/org/SomePack"
    assert result["references"] == ["Custom.image: @identity"]
    assert result["paid_api_nodes"] == ["Paid"]


def test_unknown_node_origin_is_not_silently_treated_as_builtin():
    result = WorkflowInstallerManifest().build(GRAPH, {"VAELoader": {}}, [], [], {})
    assert any("Node origin" in value for value in result["unresolved"])


def test_standalone_script_runs_without_repo_or_platform_secrets(comfy_root, monkeypatch):
    workspace = comfy_root / "workspace"
    folder = workspace / "workflows" / "chat"
    folder.mkdir(parents=True)
    path = folder / "stills.api.json"
    path.write_text(json.dumps(GRAPH))
    WorkflowDependencyRepository(workspace).record_model({"url": URL, "kind": "vae"}, "model.safetensors")
    monkeypatch.setenv("HF_TOKEN", "PLATFORM_SECRET_MUST_NOT_APPEAR")
    exporter = WorkflowInstallerExporter(workspace, get=Mock(return_value=Response(status=404)))
    artifacts, data = exporter.export(path, GRAPH, CATALOGUE)
    script = workspace / artifacts[0]
    assert "PLATFORM_SECRET_MUST_NOT_APPEAR" not in script.read_text()
    result = subprocess.run([sys.executable, "-I", str(script), "--comfy-dir", str(comfy_root), "--dry-run"],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "Dry run only" in result.stdout
    assert not (comfy_root / "models" / "vae").exists()
    assert json.loads((workspace / artifacts[1]).read_text()) == data
    path.write_text(json.dumps({}))
    changed = subprocess.run([sys.executable, "-I", str(script), "--comfy-dir", str(comfy_root), "--dry-run"],
                             text=True, capture_output=True)
    assert changed.returncode != 0
    assert "Workflow changed" in changed.stderr
    exporter.invalidate(path)
    assert "obsolete" in script.read_text()
    assert json.loads((workspace / artifacts[1]).read_text())["unresolved"]
    invalidated = subprocess.run([sys.executable, "-I", str(script)], text=True, capture_output=True)
    assert invalidated.returncode == 1 and "obsolete" in invalidated.stderr


def test_invalidation_preserves_unrelated_user_files(tmp_path):
    path = tmp_path / "stills.api.json"
    script = tmp_path / "install_stills.py"
    script.write_text("user script")
    (tmp_path / "install_stills.manifest.json").write_text('{"generator": "somebody-else"}')
    WorkflowInstallerExporter.invalidate(path)
    assert script.read_text() == "user script"


def test_recorded_sources_need_no_extra_network_requests(comfy_root):
    path = comfy_root / "workflows/chat/stills.api.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(GRAPH))
    WorkflowDependencyRepository(comfy_root).record_model({"url": URL, "kind": "vae"}, "model.safetensors")
    get = Mock(side_effect=AssertionError("no unnecessary catalogue fetch"))
    WorkflowInstallerExporter(comfy_root, get=get).export(path, GRAPH, CATALOGUE)
    get.assert_not_called()


def test_image_encoder_is_not_installed_as_text_encoder():
    graph = {"1": {"class_type": "Vision", "inputs": {"clip_name": "vision.safetensors"}}}
    catalogue = {"Vision": {"python_module": "nodes", "output": ["CLIP_VISION"],
                             "input": {"required": {"clip_name": [["vision.safetensors"]]}}}}
    data = WorkflowInstallerManifest().build(graph, catalogue, [], [{
        "filename": "vision.safetensors", "type": "clip_vision", "url": URL,
    }], {})
    assert data["models"][0]["destination"] == "models/clip_vision/vision.safetensors"


def test_conflicting_model_sources_are_not_chosen_arbitrarily():
    data = WorkflowInstallerManifest().build(GRAPH, CATALOGUE, recorded() + [
        {**recorded()[0], "url": URL.replace("/org/", "/other/")},
    ], [], {})
    assert data["unresolved"] and not data["models"]


def test_pack_alias_collisions_are_not_chosen_arbitrarily():
    graph = {"1": {"class_type": "Custom", "inputs": {}}}
    catalogue = {"Custom": {"python_module": "custom_nodes.Some_Pack"}}
    data = WorkflowInstallerManifest().build(graph, catalogue, [], [], {
        "some-pack": {"repository": "https://github.com/a/some-pack"},
        "Some_Pack": {"repository": "https://github.com/b/Some_Pack"},
    })
    assert data["unresolved"] and not data["node_packs"]


def test_download_verifies_tensor_and_second_run_skips_only_verified_file(comfy_root):
    opener = Mock()
    opener.open.return_value = DownloadResponse()
    runtime = WorkflowInstallerRuntime(comfy_root, opener=opener)
    runtime.download(model())
    target = comfy_root / "models/vae/model.safetensors"
    assert target.read_bytes() == tensor()
    runtime.download(model())
    assert opener.open.call_count == 1
    target.write_bytes(b"different")
    with pytest.raises(ValueError, match="left untouched"):
        runtime.download(model())
    assert target.read_bytes() == b"different"


@pytest.mark.parametrize("response", [
    lambda: DownloadResponse(b"<html>login</html>", {"Content-Type": "text/html"}),
    lambda: DownloadResponse(tensor()[:-1]),
    lambda: DownloadResponse(tensor(), {"Content-Length": str(len(tensor()) + 1)}),
])
def test_bad_downloads_never_publish_a_model(comfy_root, response):
    opener = Mock()
    opener.open.return_value = response()
    runtime = WorkflowInstallerRuntime(comfy_root, opener=opener)
    with pytest.raises(ValueError):
        runtime.download(model())
    assert not (comfy_root / "models/vae/model.safetensors").exists()
    assert not list((comfy_root / "models/vae").glob("*.part"))


def test_unknown_length_and_transient_retry(comfy_root):
    opener = Mock()
    opener.open.side_effect = [urllib.error.HTTPError(URL, 503, "busy", {}, None), DownloadResponse(headers={})]
    sleeper = Mock()
    WorkflowInstallerRuntime(comfy_root, opener=opener, sleep=sleeper).download(model())
    assert sleeper.call_count == 1


@pytest.mark.parametrize("url,variable", [(URL, "HF_TOKEN"), ("https://civitai.com/api/download/models/2532617", "CIVITAI_TOKEN")])
def test_auth_uses_only_users_environment_on_provider_retry(comfy_root, monkeypatch, url, variable):
    monkeypatch.setenv(variable, "USERS_KEY")
    opener = Mock()
    opener.open.side_effect = [urllib.error.HTTPError(url, 403, "no", {}, None), DownloadResponse()]
    WorkflowInstallerRuntime(comfy_root, opener=opener).download({**model(), "url": url})
    requests = [call.args[0] for call in opener.open.call_args_list]
    assert requests[0].get_header("Authorization") is None
    assert requests[1].get_header("Authorization") == "Bearer USERS_KEY"


def test_other_hosts_never_receive_provider_tokens(comfy_root, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "SECRET")
    url = "https://models.example.com/model.safetensors"
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError(url, 403, "no", {}, None)
    with pytest.raises(urllib.error.HTTPError):
        WorkflowInstallerRuntime(comfy_root, opener=opener).download({**model(), "url": url})
    assert opener.open.call_count == 1
    assert opener.open.call_args.args[0].get_header("Authorization") is None


def test_unresolved_installer_refuses_before_network_or_changes(comfy_root):
    data = manifest()
    data["unresolved"] = ["Missing source"]
    opener = Mock()
    with pytest.raises(ValueError, match="INCOMPLETE"):
        WorkflowInstallerRuntime(comfy_root, opener=opener).run(data, yes=True)
    opener.open.assert_not_called()
    assert not (comfy_root / ".workflow-installer.lock").exists()


def test_pack_install_uses_comfy_python_constraints_and_no_shell(comfy_root):
    folder = comfy_root / "custom_nodes/SomePack"
    folder.mkdir(parents=True)
    (folder / "requirements.txt").write_text("a-package")
    command = Mock(return_value=Mock(stdout="https://github.com/org/SomePack.git\n"))
    constraints = comfy_root / "constraints.txt"
    WorkflowInstallerRuntime(comfy_root, command=command).install_pack(
        {"repository": "https://github.com/org/SomePack", "directory": "SomePack"}, constraints)
    args = command.call_args.args[0]
    assert args[:4] == [sys.executable, "-m", "pip", "install"]
    assert "--constraint" in args and "--upgrade" not in args
    assert "shell" not in command.call_args.kwargs


def test_installer_never_changes_existing_python_package_versions(comfy_root, monkeypatch):
    runtime = WorkflowInstallerRuntime(comfy_root)
    data = manifest()
    data["models"] = []
    data["node_packs"] = [{"repository": "https://github.com/org/Pack"}]
    monkeypatch.setattr("workflow_installer_runtime.importlib.metadata.distributions", lambda: [
        Mock(metadata={"Name": "torch"}, version="2.8.0"),
        Mock(metadata={"Name": "numpy"}, version="2.1.0"),
    ])
    observed = []
    monkeypatch.setattr(runtime, "install_pack", lambda pack, constraints: observed.append(constraints.read_text()))
    runtime.run(data, yes=True)
    assert observed == ["torch==2.8.0\nnumpy==2.1.0"]
    assert not (comfy_root / ".workflow-installer.lock").exists()


def test_model_checksum_mismatch_never_publishes(comfy_root):
    opener = Mock()
    opener.open.return_value = DownloadResponse()
    with pytest.raises(ValueError, match="SHA256"):
        WorkflowInstallerRuntime(comfy_root, opener=opener).download({**model(), "sha256": "0" * 64})
    assert not (comfy_root / "models/vae/model.safetensors").exists()


@pytest.mark.asyncio
async def test_validation_exports_and_export_failure_does_not_fail_graph(comfy_root, monkeypatch):
    folder = comfy_root / "workflows/chat"
    folder.mkdir(parents=True)
    path = folder / "stills.api.json"
    path.write_text(json.dumps(GRAPH))
    monkeypatch.setattr(comfy_bridge, "current_workspace", lambda *args: str(comfy_root))
    monkeypatch.setattr(comfy_bridge, "_settle_downloads", AsyncMock(return_value=""))
    monkeypatch.setattr(comfy_bridge.studio_state, "mark_validated", Mock())
    monkeypatch.setattr(comfy_bridge.studio_state, "forget_validated", Mock())
    monkeypatch.setattr(comfy_bridge.WorkflowReferenceRepository, "check", Mock(return_value=[]))
    monkeypatch.setattr(comfy_bridge, "_get", Mock(return_value=Response(status=200, text=json.dumps(CATALOGUE))))
    WorkflowDependencyRepository(comfy_root).record_model({"url": URL, "kind": "vae"}, "model.safetensors")
    tool = comfy_bridge.ComfyValidateTool()
    result = await tool.execute("test", {"workflow_path": str(path)}, asyncio.Event())
    assert not result.is_error
    assert "Portable installer:" in result.content[0].text
    assert (folder / "install_stills.py").exists()
    monkeypatch.setattr(comfy_bridge.WorkflowInstallerExporter, "export", Mock(side_effect=OSError("disk")))
    result = await tool.execute("test", {"workflow_path": str(path)}, asyncio.Event())
    assert not result.is_error
    assert "validation still passed" in result.content[0].text
