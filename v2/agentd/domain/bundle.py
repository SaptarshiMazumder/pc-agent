"""Bundles — the marketplace/SKU unit (M4): "an agent is a directory" made portable.

An ``.agentpkg`` is a zip:

    <id>-<version>.agentpkg
    ├─ bundle.toml     # the manifest parsed here
    ├─ agent/          # -> unpacked to <agents_dir>/<id>/   (agent.toml, IDENTITY.md, skills/, ...)
    └─ plugins/<pid>/  # optional vendored drop-in plugins -> <plugins_dir>/<pid>/

This module is PURE domain: value objects + parsing/validation + version-compat
rules. No IO, no zip handling (infrastructure/marketplace does that).

bundle.toml shape:

    [bundle]
    id = "figure-creator"
    name = "Figure Creator"
    version = "1.0.0"
    description = "Publication-grade scientific figures."
    agentd_compat = ">=0.1,<1"      # optional; "" => any
    entitlement = ""                 # optional license SKU ("" => free)
    publisher = "agentd"

    [[bundle.plugins]]               # plugin dependencies, any mix of sources
    id = "figures"
    source = "vendored"              # "vendored" (in the zip) | "pip" | "builtin"
    # package = "agentd-plugin-x"    # pip source only
    # version = ">=1,<2"             # pip source only
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

VALID_PLUGIN_SOURCES = ("vendored", "pip", "builtin")


class BundleError(ValueError):
    """A malformed bundle manifest / registry entry (message is user-renderable)."""


def _valid_id(s: str) -> bool:
    return bool(s) and all(c.isalnum() or c in "-_" for c in s)


@dataclass(frozen=True)
class PluginDep:
    """One plugin a bundle needs. `vendored` ships in the zip; `pip` installs from
    an index; `builtin` just asserts a shipped plugin is present."""

    id: str
    source: str = "vendored"
    package: str = ""              # pip: the distribution name
    version: str = ""              # pip: a specifier ("" => latest)


@dataclass(frozen=True)
class BundleManifest:
    id: str
    name: str
    version: str
    description: str = ""
    agentd_compat: str = ""        # "" => compatible with any agentd
    entitlement: str = ""          # "" => free; else the license SKU that unlocks it
    publisher: str = ""
    plugins: tuple[PluginDep, ...] = ()


@dataclass(frozen=True)
class RegistryEntry:
    """One row of a registry index.json — enough to render a store card and to
    download + verify the artifact."""

    id: str
    name: str
    version: str
    description: str = ""
    agentd_compat: str = ""
    url: str = ""                  # absolute, or relative to the index location
    sha256: str = ""
    size: int = 0
    price: str = "free"            # informational until payments (M7+)
    entitlement: str = ""
    sig: str = ""                  # base64 ed25519 over the sha256 digest (M7)


@dataclass(frozen=True)
class RegistryIndex:
    name: str = ""
    publisher: str = ""
    publisher_key: str = ""        # base64 ed25519 public key ("" => unsigned index)
    bundles: tuple[RegistryEntry, ...] = ()


@dataclass(frozen=True)
class InstalledBundle:
    """Ledger row: what `marketplace.install` actually laid down (drives updates,
    uninstall, and 'Installed' badges in the store)."""

    id: str
    version: str
    installed_at: str = ""
    source: str = ""               # registry url / file path it came from
    plugin_ids: tuple[str, ...] = ()   # vendored plugin dirs THIS bundle placed
    entitlement: str = ""


# ---- parsing (tolerant on optionals, loud on essentials) ---------------------------


def parse_bundle_manifest(data: dict) -> BundleManifest:
    bundle = data.get("bundle")
    if not isinstance(bundle, dict):
        raise BundleError("bundle.toml: missing [bundle] table")
    bundle_id = str(bundle.get("id") or "")
    if not _valid_id(bundle_id):
        raise BundleError(f"bundle.toml: invalid id {bundle_id!r}")
    version = str(bundle.get("version") or "")
    try:
        Version(version)
    except InvalidVersion as e:
        raise BundleError(f"bundle '{bundle_id}': bad version {version!r}") from e
    deps = []
    for raw in bundle.get("plugins") or []:
        if not isinstance(raw, dict):
            continue
        dep_id = str(raw.get("id") or "")
        source = str(raw.get("source") or "vendored")
        if not _valid_id(dep_id):
            raise BundleError(f"bundle '{bundle_id}': bad plugin id {dep_id!r}")
        if source not in VALID_PLUGIN_SOURCES:
            raise BundleError(f"bundle '{bundle_id}': plugin '{dep_id}' has unknown source "
                              f"{source!r} (want one of {VALID_PLUGIN_SOURCES})")
        if source == "pip" and not raw.get("package"):
            raise BundleError(f"bundle '{bundle_id}': pip plugin '{dep_id}' needs `package`")
        deps.append(PluginDep(id=dep_id, source=source,
                              package=str(raw.get("package") or ""),
                              version=str(raw.get("version") or "")))
    return BundleManifest(
        id=bundle_id, name=str(bundle.get("name") or bundle_id), version=version,
        description=str(bundle.get("description") or ""),
        agentd_compat=str(bundle.get("agentd_compat") or ""),
        entitlement=str(bundle.get("entitlement") or ""),
        publisher=str(bundle.get("publisher") or ""),
        plugins=tuple(deps),
    )


def parse_registry_index(data: dict) -> RegistryIndex:
    if not isinstance(data, dict) or int(data.get("schema") or 1) != 1:
        raise BundleError("registry index: unsupported schema (want schema=1)")
    entries = []
    for raw in data.get("bundles") or []:
        if not isinstance(raw, dict) or not _valid_id(str(raw.get("id") or "")):
            continue
        entries.append(RegistryEntry(
            id=str(raw["id"]), name=str(raw.get("name") or raw["id"]),
            version=str(raw.get("version") or "0"),
            description=str(raw.get("description") or ""),
            agentd_compat=str(raw.get("agentd_compat") or ""),
            url=str(raw.get("url") or ""), sha256=str(raw.get("sha256") or ""),
            size=int(raw.get("size") or 0), price=str(raw.get("price") or "free"),
            entitlement=str(raw.get("entitlement") or ""), sig=str(raw.get("sig") or ""),
        ))
    return RegistryIndex(
        name=str(data.get("name") or ""), publisher=str(data.get("publisher") or ""),
        publisher_key=str(data.get("publisher_key") or ""), bundles=tuple(entries),
    )


# ---- rules ---------------------------------------------------------------------------


def compat_ok(agentd_version: str, spec: str) -> bool:
    """Does this agentd satisfy a bundle's `agentd_compat`? Empty spec => yes.
    A malformed spec is treated as NOT compatible (fail closed, message the user)."""
    if not spec.strip():
        return True
    try:
        return Version(agentd_version) in SpecifierSet(spec)
    except (InvalidSpecifier, InvalidVersion):
        return False


def is_update(installed_version: str, available_version: str) -> bool:
    try:
        return Version(available_version) > Version(installed_version)
    except InvalidVersion:
        return False
