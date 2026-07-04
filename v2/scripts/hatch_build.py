"""Hatch build hook: stage the shipped content INSIDE the package for WHEEL builds.

The wheel must carry the built-in plugin bundles and starter data in-package
(agentd/_builtin_plugins, agentd/_data) — their presence is runtime_paths'
packaged-mode marker. A checkout must NEVER have them (that would flip dev installs
into packaged mode), so:

  * standard wheel build -> stage (filtered copy), build, then remove in finalize()
  * editable build       -> do nothing (repo mode stays repo mode)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

EXCLUDED_DIRS = {"__pycache__", "node_modules", ".git", ".pytest_cache", "workspace", ".agentd"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _copy_filtered(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in item.relative_to(src).parts):
            continue
        if item.is_file() and item.suffix not in EXCLUDED_SUFFIXES:
            target = dst / item.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)


class BuiltinsStagingHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def _staged_paths(self) -> list[Path]:
        package = Path(self.root) / "agentd"
        return [package / "_builtin_plugins", package / "_data"]

    def initialize(self, version: str, build_data: dict) -> None:
        if version == "editable":
            return
        self.clean([])  # never build over a stale stage
        root = Path(self.root)
        builtins_dst, data_dst = self._staged_paths()
        _copy_filtered(root / "plugins", builtins_dst)
        soul = root / "SOUL.md"
        if soul.is_file():
            data_dst.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(soul, data_dst / "SOUL.md")
        starter_skills = root / "agents" / "main" / "skills"
        if starter_skills.is_dir():
            _copy_filtered(starter_skills, data_dst / "agents" / "main" / "skills")

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        self.clean([])

    def clean(self, versions: list[str]) -> None:
        for path in self._staged_paths():
            shutil.rmtree(path, ignore_errors=True)
