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

    def models(self):
        if not self.root.exists():
            return []
        records = []
        for path in self.root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                record["url"] = InstallerSourcePolicy.source_url(record["url"])
                record["destination"] = InstallerSourcePolicy.relative_path(record["destination"])
                records.append(record)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return records
