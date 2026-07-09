"""FileBundleInstaller — materialize/remove a bundle's files (the BundleInstaller
contract): zip-safe unpack via bundle_io, pip dependencies into THIS interpreter's
environment (why the desktop ships a real venv, not a frozen binary), and a
careful uninstall that never deletes user work by default."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

from agentd.domain.bundle import BundleError, BundleManifest, InstalledBundle
from agentd.infrastructure.marketplace import bundle_io

log = logging.getLogger("agentd")


class FileBundleInstaller:
    def __init__(
        self,
        agents_dir: Path,
        plugins_dir: Path,
        state_dir: Path,
        builtin_plugins_dir: Path | None = None,
    ):
        self._agents_dir = agents_dir
        self._plugins_dir = plugins_dir
        self._state_dir = state_dir
        self._builtin_plugins_dir = builtin_plugins_dir

    def read_manifest(self, package_path: Path) -> BundleManifest:
        return bundle_io.read_manifest(package_path)

    async def install_files(self, package_path: Path, manifest: BundleManifest) -> list[str]:
        self._check_builtin_deps(manifest)
        placed = await asyncio.to_thread(
            bundle_io.unpack_bundle, package_path, manifest, self._agents_dir, self._plugins_dir
        )
        vendored_declared = {d.id for d in manifest.plugins if d.source == "vendored"}
        missing = vendored_declared - set(placed)
        if missing:
            raise BundleError(
                f"'{manifest.id}': manifest declares vendored plugin(s) the zip "
                f"doesn't contain: {', '.join(sorted(missing))}"
            )
        for dep in manifest.plugins:
            if dep.source == "pip":
                await self._pip_install(dep.package, dep.version)
        return placed

    async def remove_files(
        self, installed: InstalledBundle, keep_plugin_ids: set[str], purge_state: bool = False
    ) -> None:
        agent_dir = self._agents_dir / installed.id
        if agent_dir.is_dir():
            for child in agent_dir.iterdir():
                # the user's files survive a plain uninstall; --purge removes everything
                if child.name == "workspace" and not purge_state:
                    continue
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
            # a workspace holding no actual files is just runtime scaffolding — leaving it
            # would keep a GHOST agent dir the registry re-lists after uninstall
            workspace = agent_dir / "workspace"
            if (
                not purge_state
                and workspace.is_dir()
                and not any(f.is_file() for f in workspace.rglob("*"))
            ):
                shutil.rmtree(workspace, ignore_errors=True)
            if purge_state or not any(agent_dir.iterdir()):
                shutil.rmtree(agent_dir, ignore_errors=True)
        for plugin_id in installed.plugin_ids:
            if plugin_id in keep_plugin_ids:
                log.info(
                    "uninstall %s: keeping plugin '%s' (another bundle uses it)",
                    installed.id,
                    plugin_id,
                )
                continue
            shutil.rmtree(self._plugins_dir / plugin_id, ignore_errors=True)
        if purge_state:
            shutil.rmtree(self._state_dir / "agents" / installed.id, ignore_errors=True)

    # ------------------------------------------------------------ internals

    def _check_builtin_deps(self, manifest: BundleManifest) -> None:
        """`builtin` deps assert a shipped plugin exists in THIS install (a Studio
        flavor may ship fewer built-ins than the full repo)."""
        if self._builtin_plugins_dir is None:
            return
        for dep in manifest.plugins:
            if dep.source != "builtin":
                continue
            for root in (self._builtin_plugins_dir, self._plugins_dir):
                if (root / dep.id / "plugin.toml").is_file():
                    break
            else:
                raise BundleError(
                    f"'{manifest.id}' needs built-in plugin '{dep.id}', which this "
                    f"install does not ship"
                )

    async def _pip_install(self, package: str, version_spec: str) -> None:
        requirement = f"{package}{version_spec}" if version_spec else package
        log.info("pip install %s (into %s)", requirement, sys.prefix)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            requirement,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        if process.returncode != 0:
            tail = output.decode(errors="replace").strip().splitlines()[-8:]
            raise BundleError(f"pip install {requirement} failed:\n" + "\n".join(tail))
