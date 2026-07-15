"""Hatch build hook: ship the built-in content INSIDE the package for WHEEL builds.

The wheel must carry the built-in plugin bundles and starter data in-package
(agentd/_builtin_plugins, agentd/_data) — their presence is runtime_paths'
packaged-mode marker. A checkout must NEVER contain those dirs (that would flip dev
installs into packaged mode), so nothing is staged inside the repo: a filtered copy
goes to a TEMP directory and is mapped into the wheel via ``build_data
["force_include"]`` (which also bypasses VCS-ignore rules hatchling applies to
ordinary includes). Editable builds get no staging at all — repo mode stays repo mode.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

EXCLUDED_DIRS = {"__pycache__", "node_modules", ".git", ".pytest_cache", "workspace", ".agentd"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _copy_filtered(src: Path, dst: Path) -> int:
    copied = 0
    for item in src.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in item.relative_to(src).parts):
            continue
        if item.is_file() and item.suffix not in EXCLUDED_SUFFIXES:
            target = dst / item.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)
            copied += 1
    return copied


class BuiltinsStagingHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    _staging: Path | None = None

    def initialize(self, version: str, build_data: dict) -> None:
        if version == "editable":
            return
        root = Path(self.root)
        self._staging = Path(tempfile.mkdtemp(prefix="agentd-wheel-stage-"))
        builtins_dst = self._staging / "_builtin_plugins"
        data_dst = self._staging / "_data"

        plugin_count = _copy_filtered(root / "plugins", builtins_dst)
        soul = root / "SOUL.md"
        if soul.is_file():
            data_dst.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(soul, data_dst / "SOUL.md")
        # Ship the default config template (full model_catalog + neutral knobs) so first_run
        # seeds a REAL config on a fresh install — not a bare {model} stub with an empty
        # model picker. Read back by runtime_paths.packaged_default_config().
        default_cfg = root / "config.example.json"
        if default_cfg.is_file():
            data_dst.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(default_cfg, data_dst / "config.default.json")
        starter_skills = root / "agents" / "main" / "skills"
        if starter_skills.is_dir():
            _copy_filtered(starter_skills, data_dst / "agents" / "main" / "skills")

        force_include = build_data.setdefault("force_include", {})
        force_include[str(builtins_dst)] = "agentd/_builtin_plugins"
        force_include[str(data_dst)] = "agentd/_data"
        self.app.display_info(
            f"agentd wheel: staged {plugin_count} built-in plugin file(s) + starter data"
        )

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        self._cleanup()

    def clean(self, versions: list[str]) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        if self._staging is not None:
            shutil.rmtree(self._staging, ignore_errors=True)
            self._staging = None
