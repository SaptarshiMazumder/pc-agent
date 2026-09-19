"""Write a portable, fixed-code installer and its inspectable dependency manifest."""

import json
from pathlib import Path

from workflow_dependency_repository import WorkflowDependencyRepository
from workflow_installer_manifest import WorkflowInstallerManifest


class WorkflowInstallerExporter:
    SOURCES = ("installer_source_policy", "model_download_request", "model_download_redirect_policy",
               "gpu_download_activity", "model_download_resume_state",
               "gpu_model_download_worker", "workflow_installer_runtime")

    def __init__(self, workspace, *, get):
        self.workspace = Path(workspace).resolve()
        self.get = get

    @staticmethod
    def invalidate(path):
        """Disable stale exports by writing: hosted sandbox deletions do not sync back."""
        path = Path(path)
        name = path.name.removesuffix(".api.json")
        manifest_path = path.with_name(f"install_{name}.manifest.json")
        if not manifest_path.exists():
            return
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            return  # not an intact exporter-owned manifest
        if not isinstance(data, dict):
            return
        if data.get("generator") != "comfy-workflow-installer" or data.get("workflow") != path.name:
            return
        message = "Installer is obsolete: re-validate the workflow and download the new installer."
        data["unresolved"] = [message]
        data["models"] = []
        data["node_packs"] = []
        path.with_name(f"install_{name}.py").write_text(
            "# This workflow's previous installer is obsolete.\nimport sys\nsys.exit(" + repr(message) + ")\n",
            encoding="utf-8",
        )
        manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _catalogue(self, path, key, default):
        try:
            response = self.get(path, timeout_s=15)
            value = response.json().get(key, default) if response.ok else default
            return value if isinstance(value, type(default)) else default
        except Exception:
            return default  # missing metadata becomes an explicit unresolved dependency

    def export(self, path, graph, catalogue):
        target = Path(path).resolve()
        if not target.is_relative_to(self.workspace / "workflows") or not target.name.endswith(".api.json"):
            raise ValueError("Installer exports must accompany an emitted workflow inside workflows/")
        builder = WorkflowInstallerManifest()
        recorded = WorkflowDependencyRepository(self.workspace).models()
        initial = builder.build(graph, catalogue, recorded, [], {})
        models = (self._catalogue("/externalmodel/getlist?mode=cache", "models", [])
                  if any(item.startswith("Model source") for item in initial["unresolved"]) else [])
        packs = (self._catalogue("/customnode/getlist?mode=cache", "node_packs", {})
                 if any(item.startswith("Node pack source") for item in initial["unresolved"]) else {})
        manifest = builder.build(graph, catalogue, recorded, models, packs)
        manifest["workflow"] = target.name
        manifest["generator"] = "comfy-workflow-installer"
        name = target.name.removesuffix(".api.json")
        manifest_path = target.with_name(f"install_{name}.manifest.json")
        script_path = target.with_name(f"install_{name}.py")
        sources = {f"{name}.py": (Path(__file__).parent / f"{name}.py").read_text(encoding="utf-8")
                   for name in self.SOURCES}
        # The manifest is DATA, never interpolated into commands or generated Python syntax.
        script = (
            '#!/usr/bin/env python3\n"""Generated ComfyUI dependency installer (Python 3.10+).\n'
            "Use ComfyUI's Python: python install_NAME.py --comfy-dir /path/to/ComfyUI --dry-run\n"
            'Remove --dry-run to install. Stop ComfyUI first. No rendering or paid API calls.\n"""\n'
            'import hashlib, json, sys, tempfile\nfrom pathlib import Path\n'
            f'MANIFEST = json.loads({json.dumps(manifest, ensure_ascii=True)!r})\n'
            f'SOURCES = {sources!r}\n'
            'if __name__ == "__main__":\n'
            '    workflow = Path(__file__).parent / MANIFEST["workflow"]\n'
            '    if workflow.exists():\n'
            '        digest = hashlib.sha256(json.dumps(json.loads(workflow.read_text(encoding="utf-8")), sort_keys=True).encode()).hexdigest()\n'
            '        if digest != MANIFEST["workflow_sha256"]:\n'
            '            sys.exit("Workflow changed since this installer was exported. Re-validate and download a fresh installer.")\n'
            '    with tempfile.TemporaryDirectory(prefix="comfy-workflow-installer-") as directory:\n'
            '        for name, source in SOURCES.items():\n'
            '            (Path(directory) / name).write_text(source, encoding="utf-8")\n'
            '        sys.path.insert(0, directory)\n'
            '        from workflow_installer_runtime import WorkflowInstallerRuntime\n'
            '        sys.exit(WorkflowInstallerRuntime.main(MANIFEST))\n'
        )
        compile(script, script_path.name, "exec")
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        script_path.write_text(script, encoding="utf-8")
        return [p.relative_to(self.workspace).as_posix() for p in (script_path, manifest_path)], manifest
