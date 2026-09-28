"""Assemble a template's setup guide from the chat it is saved from.

WHERE THE SOURCES COME FROM. Each workflow in the chat has an installer list
(`install_<role>.manifest.json`), written when it was validated from what the machine reported,
Manager's catalogue and every source this workspace actually installed from — models through
comfy_install, and now node packs installed from their repositories too
(WorkflowDependencyRepository). The guide is those lists merged; what none of them could source
comes back as GAPS, which the Save dialog asks the person to fill before the template is kept.
"""

from __future__ import annotations

import json
from pathlib import Path

from template_setup_guide import SetupGap, TemplateSetupGuide


class TemplateSetupGuideBuilder:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()

    def build(self, manifest_paths: list[str]) -> tuple[TemplateSetupGuide, list[SetupGap]]:
        manifests = [self._read(p) for p in manifest_paths]
        guide = TemplateSetupGuide.from_manifests(manifests)
        return guide, guide.gaps(manifests)

    def _read(self, rel: str) -> dict:
        path = (self.workspace / rel).resolve()
        if not path.is_relative_to(self.workspace / "workflows") or not path.name.endswith(".manifest.json"):
            raise ValueError(f"{rel} is not a workflow's installer list")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{rel} is not an installer list")
        return data
