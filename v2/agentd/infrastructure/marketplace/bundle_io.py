"""Bundle IO — pack / read / unpack .agentpkg zips, safely.

Layout inside the zip (see domain/bundle.py): bundle.toml + agent/ + plugins/<id>/.
Unpacking guards against zip-slip (absolute paths, ..) — a marketplace artifact is
untrusted input even when it came from our own registry."""

from __future__ import annotations

import hashlib
import logging
import shutil
import tomllib
import zipfile
from pathlib import Path

from agentd.domain.bundle import BundleError, BundleManifest, parse_bundle_manifest

log = logging.getLogger("agentd")

# never packed: runtime junk + user data that must not ship inside an artifact
EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    "node_modules",
    "workspace",
    "sessions",
    ".agentd",
    # BUILT PRODUCTS delivered into the agent's own folder (agents/<id>/clients/…, e.g. its
    # desktop installer exe). Derived FROM the package — packing them back would nest the
    # product inside its own source artifact.
    "clients",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(package_path: Path) -> BundleManifest:
    try:
        with zipfile.ZipFile(package_path) as zf:
            raw = zf.read("bundle.toml")
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise BundleError(f"not a valid .agentpkg (no readable bundle.toml): {package_path}") from e
    try:
        return parse_bundle_manifest(tomllib.loads(raw.decode("utf-8")))
    except tomllib.TOMLDecodeError as e:
        raise BundleError(f"bundle.toml is not valid TOML: {e}") from e


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise BundleError(f"refusing bundle with unsafe path: {info.filename!r}")
        members.append(info)
    return members


def unpack_bundle(
    package_path: Path, manifest: BundleManifest, agents_dir: Path, plugins_dir: Path
) -> list[str]:
    """agent/** -> agents_dir/<bundle id>/ ; plugins/<pid>/** -> plugins_dir/<pid>/.
    Existing agent dir is REPLACED except its workspace/ (user files survive
    updates). Returns the vendored plugin ids actually placed."""
    agent_dst = agents_dir / manifest.id
    if agent_dst.exists():  # update: clear the definition, keep the user's workspace
        for child in agent_dst.iterdir():
            if child.name == "workspace":
                continue
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
    placed_plugins: set[str] = set()
    with zipfile.ZipFile(package_path) as zf:
        for info in _safe_members(zf):
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            parts = Path(name).parts
            if parts[0] == "agent" and len(parts) > 1:
                target = agent_dst / Path(*parts[1:])
            elif parts[0] == "plugins" and len(parts) > 2:
                plugin_id = parts[1]
                target = plugins_dir / plugin_id / Path(*parts[2:])
                placed_plugins.add(plugin_id)
            else:
                continue  # bundle.toml + anything unrecognized stays in the zip
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    log.info(
        "bundle %s: unpacked agent -> %s%s",
        manifest.id,
        agent_dst,
        f", plugins: {', '.join(sorted(placed_plugins))}" if placed_plugins else "",
    )
    return sorted(placed_plugins)


def pack_bundle(
    agent_dir: Path,
    out_dir: Path,
    manifest: BundleManifest,
    vendored_plugin_dirs: dict[str, Path] | None = None,
) -> Path:
    """agent_dir + vendored plugin dirs -> <out_dir>/<id>-<version>.agentpkg.
    The manifest is serialized to bundle.toml inside the zip (single source)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    package_path = out_dir / f"{manifest.id}-{manifest.version}.agentpkg"
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.toml", _manifest_toml(manifest))
        for src in _iter_files(agent_dir):
            zf.write(src, Path("agent") / src.relative_to(agent_dir))
        for plugin_id, plugin_dir in (vendored_plugin_dirs or {}).items():
            for src in _iter_files(plugin_dir):
                zf.write(src, Path("plugins") / plugin_id / src.relative_to(plugin_dir))
    log.info("packed %s (%d bytes)", package_path.name, package_path.stat().st_size)
    return package_path


def _iter_files(root: Path):
    for item in sorted(root.rglob("*")):
        relative_parts = item.relative_to(root).parts
        if any(part in EXCLUDED_DIRS for part in relative_parts):
            continue
        if item.is_file() and item.suffix not in EXCLUDED_SUFFIXES:
            yield item


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _manifest_toml(manifest: BundleManifest) -> str:
    lines = [
        "# generated by `agentd bundle pack` — the bundle's identity + dependencies",
        "[bundle]",
        f"id = {_toml_str(manifest.id)}",
        f"name = {_toml_str(manifest.name)}",
        f"version = {_toml_str(manifest.version)}",
        f"description = {_toml_str(manifest.description)}",
        f"agentd_compat = {_toml_str(manifest.agentd_compat)}",
        f"entitlement = {_toml_str(manifest.entitlement)}",
        f"publisher = {_toml_str(manifest.publisher)}",
    ]
    for dep in manifest.plugins:
        lines += [
            "",
            "[[bundle.plugins]]",
            f"id = {_toml_str(dep.id)}",
            f"source = {_toml_str(dep.source)}",
        ]
        if dep.package:
            lines.append(f"package = {_toml_str(dep.package)}")
        if dep.version:
            lines.append(f"version = {_toml_str(dep.version)}")
    return "\n".join(lines) + "\n"
