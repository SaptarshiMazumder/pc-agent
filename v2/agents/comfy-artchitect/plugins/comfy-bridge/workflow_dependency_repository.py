"""Workspace persistence of observed dependency sources, one record per destination."""

import hashlib
import json
from pathlib import Path

from installer_source_policy import InstallerSourcePolicy
from model_download_request import ModelDownloadRequest


class WorkflowDependencyRepository:
    def __init__(self, workspace):
        self.root = Path(workspace) / ".studio" / "installer-sources"

    def record_model(self, file, installed_name):
        directory = ModelDownloadRequest.DIRECTORIES.get(file["kind"])
        if not directory:
            raise ValueError("Unsupported model destination")
        destination = InstallerSourcePolicy.relative_path(f"models/{directory}/{installed_name}")
        record = {"destination": destination, "url": InstallerSourcePolicy.source_url(file["url"]),
                  "filename": installed_name, "kind": directory, "evidence": "comfy_install"}
        digest = hashlib.sha256(destination.encode()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{digest}.json").write_text(json.dumps(record), encoding="utf-8")

    def record_node_pack(self, repository, folder):
        """A node pack installed from its repository (not Manager's registry, which already knows
        its own) — recorded under the folder it landed in, which is the module name every one of
        its node classes reports, so an installer and a template's setup guide can name it."""
        repository = InstallerSourcePolicy.repository_url(repository)
        folder = InstallerSourcePolicy.relative_path(str(folder))
        record = {"repository": repository, "folder": folder, "evidence": "comfy_node_install"}
        self.root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(f"pack:{folder}".encode()).hexdigest()
        (self.root / f"pack-{digest}.json").write_text(json.dumps(record), encoding="utf-8")

    def node_packs(self):
        """{folder: {"repository": url}} — the shape of Manager's own pack list, so the installer
        manifest matches them the same way."""
        if not self.root.exists():
            return {}
        packs = {}
        for path in self.root.glob("pack-*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                packs[record["folder"]] = {"repository": InstallerSourcePolicy.repository_url(record["repository"])}
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return packs

    def models(self):
        if not self.root.exists():
            return []
        records = []
        for path in self.root.glob("*.json"):
            if path.name.startswith("pack-"):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                record["url"] = InstallerSourcePolicy.source_url(record["url"])
                record["destination"] = InstallerSourcePolicy.relative_path(record["destination"])
                records.append(record)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return records
