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

from agent_runtime.domain.bundle import BundleError, BundleManifest, parse_bundle_manifest

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
# The runtime's per-agent ownership record (domain/ownership.py). A bundle carries the AUTHOR's
# files; ownership of a copy is decided where the copy lands — the installer stamps a fresh
# record on arrival. Packing it would ship the author's identity into every install.
EXCLUDED_FILES = {".agentd-meta.json"}


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


def read_agent_file(package_path: Path, relative: str) -> bytes | None:
    """One file out of a package's ``agent/`` tree, or None when it is not in there.

    Building a PRODUCT from a published .agentpkg (the only thing a publish service ever has) needs
    the agent's own declaration and icon, and both are inside the zip. Reading them without
    unpacking to a temp dir keeps that path from needing somewhere writable.

    Returns None rather than raising for a missing member: "this agent ships no icon" is an
    ordinary answer. A corrupt or unreadable archive still raises, because that is not.
    """
    member = f"agent/{relative.strip().lstrip('/').replace(chr(92), '/')}"
    try:
        with zipfile.ZipFile(package_path) as zf:
            _safe_members(zf)  # same path-traversal refusal as unpacking
            try:
                return zf.read(member)
            except KeyError:
                return None
    except (zipfile.BadZipFile, OSError) as e:
        raise BundleError(f"not a readable .agentpkg: {package_path}") from e


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
    Existing agent dir is REPLACED except the USER'S OWN subtrees (workspace/ and sessions/),
    so files and chat history survive an update. Returns the vendored plugin ids placed."""
    from agent_runtime.domain.agent import USER_DATA_DIRS

    agent_dst = agents_dir / manifest.id
    if agent_dst.exists():  # update: clear the definition, keep what is the user's
        for child in agent_dst.iterdir():
            if child.name in USER_DATA_DIRS:
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


#: Where an agent app loads the client SDK from, relative to the agent directory.
VENDORED_SDK = Path("ui") / "vendor" / "agentd-client.js"


def _canonical_sdk() -> bytes | None:
    """The SDK build this ENGINE ships, or None when it ships none."""
    try:
        from agent_runtime.runtime_paths import sdk_client_asset

        asset = sdk_client_asset()
        return asset.read_bytes() if asset else None
    except Exception:  # noqa: BLE001 — a missing asset must never block packing
        return None


def pack_bundle(
    agent_dir: Path,
    out_dir: Path,
    manifest: BundleManifest,
    vendored_plugin_dirs: dict[str, Path] | None = None,
) -> Path:
    """agent_dir + vendored plugin dirs -> <out_dir>/<id>-<version>.agentpkg.
    The manifest is serialized to bundle.toml inside the zip (single source).

    THE VENDORED SDK IS RE-VENDORED HERE, from the build this engine ships. `ui/vendor/
    agentd-client.js` is COPIED into every agent at scaffold time, so one SDK fix has to reach
    every copy that exists anywhere — repo agents, agents authored in an account directory,
    already-installed agents. The copies that get missed do not fail loudly; they fail later as
    "that method is not a function", or as a sign-in that silently reads a response field the
    server stopped sending. Substituting at pack time makes a stale package impossible: whatever
    is on disk, the package carries the SDK that matches the engine that packed it.

    The author's own files are NOT modified. Packing rewriting your source would be a surprise,
    and the thing that actually has to be current is the artifact.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    package_path = out_dir / f"{manifest.id}-{manifest.version}.agentpkg"
    sdk = _canonical_sdk()
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.toml", _manifest_toml(manifest))
        for src in _iter_files(agent_dir):
            rel = src.relative_to(agent_dir)
            if sdk is not None and rel == VENDORED_SDK:
                if src.read_bytes() != sdk:
                    log.info("re-vendored %s from this engine's SDK build", VENDORED_SDK.as_posix())
                # as_posix(): zipfile normalises separators for write() but NOT for writestr(),
                # and a backslash in a zip entry name is a path component on POSIX readers.
                zf.writestr((Path("agent") / rel).as_posix(), sdk)
                continue
            zf.write(src, Path("agent") / rel)
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
        if item.name in EXCLUDED_FILES:
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
        f"icon = {_toml_str(manifest.icon)}",
        # Always written, both keys, even at their defaults: the publish service re-reads THIS
        # copy (not the author's file), so an omitted key here would silently reset an author's
        # choice on every pack.
        "",
        "[bundle.delivery]",
        f"web = {str(manifest.delivery.web).lower()}",
        f"exe = {str(manifest.delivery.exe).lower()}",
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
