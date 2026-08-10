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
    icon = "sparkles"                # optional store-card glyph name ("" => client default)

    [bundle.delivery]                # optional: HOW this agent reaches people (see DeliveryModes)
    web = false                      # listed with an Open-in-browser link (requires [app] in agent.toml)
    exe = true                       # a per-agent installer is built at publish

    [[bundle.plugins]]               # plugin dependencies, any mix of sources
    id = "figures"
    source = "vendored"              # "vendored" (in the zip) | "pip" | "builtin"
    # package = "agentd-plugin-x"    # pip source only
    # version = ">=1,<2"             # pip source only
"""

from __future__ import annotations

from dataclasses import dataclass

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from agent_runtime.domain.product import EngineRef

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
    package: str = ""  # pip: the distribution name
    version: str = ""  # pip: a specifier ("" => latest)


@dataclass(frozen=True)
class DeliveryModes:
    """HOW an agent reaches people — the author's choice, carried from bundle.toml to the store.

    Installing into a running agentd is not listed here because it is not optional: a bundle IS
    the thing a daemon installs. These are the two EXTRA doors:

      * ``web`` — the hosted deployment serves the agent's app at ``/apps/<id>`` and the store
        shows **Open in browser**. Opt-in, never inferred: a web agent runs on the platform's
        infrastructure, so silence must mean "no".
      * ``exe`` — the publish service compiles a per-agent installer and the store shows
        **Download**. Defaults on because it was the only behaviour before this field existed,
        and every already-published bundle must keep meaning what it meant.
    """

    web: bool = False
    exe: bool = True


def _parse_delivery(raw) -> DeliveryModes:
    """Tolerant like ``installers``: this key postdates shipped clients, so junk in it must
    degrade to the defaults rather than blank a store."""
    if not isinstance(raw, dict):
        return DeliveryModes()
    return DeliveryModes(web=bool(raw.get("web", False)), exe=bool(raw.get("exe", True)))


@dataclass(frozen=True)
class BundleManifest:
    id: str
    name: str
    version: str
    description: str = ""
    agentd_compat: str = ""  # "" => compatible with any agentd
    entitlement: str = ""  # "" => free; else the license SKU that unlocks it
    publisher: str = ""
    icon: str = ""  # store-card glyph name ("" => client default)
    plugins: tuple[PluginDep, ...] = ()
    delivery: DeliveryModes = DeliveryModes()


@dataclass(frozen=True)
class InstallerAsset:
    """A downloadable INSTALLER for one platform — the artifact a person with no agentd yet runs.

    Distinct from ``RegistryEntry.url`` (the .agentpkg), and the distinction is the whole point:
    a .agentpkg is installed BY a running daemon, so it only reaches someone who already has the
    product. An installer is what a browser downloads on a bare machine. Without this the
    marketplace could only ever sell to existing users.

    ``sha256`` is signed separately from the bundle's (see index_builder) because the entry
    signature covers only the .agentpkg digest — leaving the installer rows unsigned would let
    anyone who can write to the registry swap the executable without breaking any signature.
    Note the limit honestly: a BROWSER download verifies nothing. Only a client that downloads
    through the daemon checks this. Code signing the executable is the real fix for browsers.
    """

    platform: str  # "win" | "mac" | "linux" — matched against the client's own platform
    url: str = ""  # absolute, or relative to the index location (same rule as the bundle url)
    size: int = 0
    sha256: str = ""
    sig: str = ""  # base64 ed25519 over THIS asset's sha256


@dataclass(frozen=True)
class RegistryEntry:
    """One row of a registry index.json — enough to render a store card and to
    download + verify the artifact."""

    id: str
    name: str
    version: str
    description: str = ""
    agentd_compat: str = ""
    url: str = ""  # absolute, or relative to the index location
    sha256: str = ""
    size: int = 0
    price: str = "free"  # informational until payments (M7+)
    entitlement: str = ""
    icon: str = ""  # store-card glyph name ("" => client default)
    sig: str = ""  # base64 ed25519 over the sha256 digest (M7)
    publisher_id: str = ""  # schema 2: WHOSE key signed it (a roster id). "" => the index's own key
    installers: tuple[InstallerAsset, ...] = ()  # standalone downloads, one per platform
    delivery: DeliveryModes = DeliveryModes()  # which store actions this bundle offers


def _parse_installers(raw) -> tuple[InstallerAsset, ...]:
    """Parse the optional ``installers`` array. Malformed or url-less rows are DROPPED, not
    raised on: this key was added after clients shipped, so tolerating junk in it is what keeps a
    future registry from blanking the store on an older install. A row with no platform or no url
    could not be offered as a download anyway."""
    if not isinstance(raw, list):
        return ()
    out = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        platform = str(row.get("platform") or "").strip().lower()
        url = str(row.get("url") or "").strip()
        if not platform or not url:
            continue
        try:
            size = int(row.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append(
            InstallerAsset(
                platform=platform,
                url=url,
                size=size,
                sha256=str(row.get("sha256") or ""),
                sig=str(row.get("sig") or ""),
            )
        )
    return tuple(out)


# ── The trust roster (schema 2) ─────────────────────────────────────────────────────────
#
# THE PROBLEM IT SOLVES. In schema 1 a registry has exactly one publisher key, and every client
# pins it at install time. A second creator therefore cannot publish: their signature would be
# rejected by every installed client, and the only fix would be re-pinning — a new build, shipped
# to everyone, per creator. That is not a marketplace.
#
# THE ANSWER. Clients still pin exactly ONE key: the platform ROOT key. The root key does not sign
# bundles. It signs a ROSTER — the list of creators and their public keys — and each creator signs
# their own bundles with their own key, which never leaves them. Adding a creator is a roster edit;
# so is revoking one. No client ever re-pins anything.
#
# WHAT THIS DOES NOT PROVIDE, stated because the gap is real and easy to assume away: the roster is
# signed, and each BUNDLE is signed, but the index as a whole is not. Someone who can rewrite
# index.json can therefore REMOVE an entry or REPLAY an older one. They cannot forge a bundle or
# add a creator. `issued` plus the client's memory of the newest roster it has seen bounds the
# replay; a signed index manifest is the real fix, and is not in this change.


@dataclass(frozen=True)
class PublisherEntry:
    """One creator on the roster: a stable id and the public key their bundles are signed with."""

    id: str
    key: str  # base64 ed25519 public key
    name: str = ""
    added: str = ""  # ISO-8601, informational


@dataclass(frozen=True)
class PublisherRoster:
    entries: tuple[PublisherEntry, ...] = ()
    revoked: tuple[str, ...] = ()
    issued: str = ""  # ISO-8601; monotonic — a client rejects a roster older than one it has seen
    sig: str = ""  # base64 ed25519 over roster_signing_payload(...), by the PLATFORM ROOT key

    def trusted_keys(self) -> dict[str, str]:
        """id -> public key, revocations removed. The map bundle verification looks a creator up in.

        Revocation is applied HERE rather than by deleting the entry, so a revoked creator's row
        stays visible in the file (and in `agentd bundle roster show`). A silently deleted creator
        and a creator who was never there look identical, which is the wrong thing to be unable to
        tell apart during an incident.
        """
        revoked = {r.strip() for r in self.revoked if r.strip()}
        return {e.id: e.key for e in self.entries if e.id and e.key and e.id not in revoked}


def roster_signing_payload(
    entries: tuple[PublisherEntry, ...], revoked: tuple[str, ...], issued: str
) -> bytes:
    """The EXACT bytes the root key signs. Canonical, and it has to be.

    Both sides serialize this independently — the publisher when signing, the client when checking
    — so any disagreement about key order, spacing or field set is a signature that never verifies,
    on a path where "invalid signature" is indistinguishable from an actual attack. Sorted keys, no
    spaces, entries sorted by id, and only the four fields that matter.
    """
    import json

    payload = {
        "issued": issued or "",
        "revoked": sorted(r for r in revoked if r),
        "roster": sorted(
            (
                {"added": e.added or "", "id": e.id, "key": e.key, "name": e.name or ""}
                for e in entries
            ),
            key=lambda e: e["id"],
        ),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_publisher_roster(data: dict) -> PublisherRoster:
    if not isinstance(data, dict):
        raise BundleError("registry index: `publishers` is not an object")
    entries = []
    for raw in data.get("roster") or []:
        if not isinstance(raw, dict):
            continue
        pid = str(raw.get("id") or "").strip()
        key = str(raw.get("key") or "").strip()
        if not _valid_id(pid) or not key:
            continue
        entries.append(
            PublisherEntry(
                id=pid, key=key, name=str(raw.get("name") or pid), added=str(raw.get("added") or "")
            )
        )
    return PublisherRoster(
        entries=tuple(entries),
        revoked=tuple(str(r).strip() for r in (data.get("revoked") or []) if str(r).strip()),
        issued=str(data.get("issued") or ""),
        sig=str(data.get("sig") or ""),
    )


def _parse_engines(raw) -> tuple[EngineRef, ...]:
    """Parse the optional ``engine`` array: the shared ENGINE builds, one row per platform.

    Why the registry describes the engine at all. A per-agent installer is a small stub that has to
    be able to fetch the shared engine on a machine that has none — so it needs a url and a digest,
    and those change every engine release while stubs built last month keep circulating. Publishing
    them here means a stub is built against whatever is current at BUILD time, and the engine can be
    re-released without rebuilding a single stub.

    Same tolerance as ``installers`` and for the same reason: this key was added after clients
    shipped, so junk in it must not blank the store on an older install.
    """
    if not isinstance(raw, list):
        return ()
    out = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        platform = str(row.get("platform") or "").strip().lower()
        url = str(row.get("url") or "").strip()
        if not platform or not url:
            continue
        try:
            size = int(row.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append(
            EngineRef(
                platform=platform,
                version=str(row.get("version") or ""),
                url=url,
                size=size,
                sha256=str(row.get("sha256") or ""),
                sig=str(row.get("sig") or ""),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class RegistryIndex:
    name: str = ""
    publisher: str = ""
    publisher_key: str = ""  # schema 1: the one publisher key. schema 2: the PLATFORM ROOT key.
    bundles: tuple[RegistryEntry, ...] = ()
    schema: int = 1
    publishers: PublisherRoster | None = None  # schema 2 only
    engines: tuple[EngineRef, ...] = ()  # the shared engine builds a stub can install
    # Where web-delivered agents run: the hosted deployment's base url ("" => none known, so no
    # Open-in-browser links anywhere). Registry-level like `engine`, and for the same reason —
    # the deployment moves independently of every published bundle, and clients (including the
    # DESKTOP store, which is not that deployment) learn it from here rather than being built
    # with it.
    web_host: str = ""

    def engine_for(self, platform: str) -> EngineRef | None:
        want = (platform or "").strip().lower()
        return next((e for e in self.engines if e.platform == want), None)


@dataclass(frozen=True)
class InstalledBundle:
    """Ledger row: what `marketplace.install` actually laid down (drives updates,
    uninstall, and 'Installed' badges in the store)."""

    id: str
    version: str
    installed_at: str = ""
    source: str = ""  # registry url / file path it came from
    plugin_ids: tuple[str, ...] = ()  # vendored plugin dirs THIS bundle placed
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
            raise BundleError(
                f"bundle '{bundle_id}': plugin '{dep_id}' has unknown source "
                f"{source!r} (want one of {VALID_PLUGIN_SOURCES})"
            )
        if source == "pip" and not raw.get("package"):
            raise BundleError(f"bundle '{bundle_id}': pip plugin '{dep_id}' needs `package`")
        deps.append(
            PluginDep(
                id=dep_id,
                source=source,
                package=str(raw.get("package") or ""),
                version=str(raw.get("version") or ""),
            )
        )
    return BundleManifest(
        id=bundle_id,
        name=str(bundle.get("name") or bundle_id),
        version=version,
        description=str(bundle.get("description") or ""),
        agentd_compat=str(bundle.get("agentd_compat") or ""),
        entitlement=str(bundle.get("entitlement") or ""),
        publisher=str(bundle.get("publisher") or ""),
        icon=str(bundle.get("icon") or ""),
        plugins=tuple(deps),
        delivery=_parse_delivery(bundle.get("delivery")),
    )


SUPPORTED_INDEX_SCHEMAS = (1, 2)


def parse_registry_index(data: dict) -> RegistryIndex:
    """Schema 1 (one publisher key) and schema 2 (a signed roster of creators) both parse here.

    Schema 1 keeps working forever, or at least for as long as clients built before schema 2
    existed are still installed — which, for a downloadable desktop app, is "indefinitely". An
    already-installed client that met an index it could not parse would show an empty store with
    no explanation, so the compatibility is not a courtesy.
    """
    if not isinstance(data, dict):
        raise BundleError("registry index: not a JSON object")
    try:
        schema = int(data.get("schema") or 1)
    except (TypeError, ValueError):
        raise BundleError("registry index: `schema` is not a number") from None
    if schema not in SUPPORTED_INDEX_SCHEMAS:
        raise BundleError(
            f"registry index: unsupported schema {schema} (this agentd understands "
            f"{', '.join(str(s) for s in SUPPORTED_INDEX_SCHEMAS)}) — update agentd"
        )
    entries = []
    for raw in data.get("bundles") or []:
        if not isinstance(raw, dict) or not _valid_id(str(raw.get("id") or "")):
            continue
        entries.append(
            RegistryEntry(
                id=str(raw["id"]),
                name=str(raw.get("name") or raw["id"]),
                version=str(raw.get("version") or "0"),
                description=str(raw.get("description") or ""),
                agentd_compat=str(raw.get("agentd_compat") or ""),
                url=str(raw.get("url") or ""),
                sha256=str(raw.get("sha256") or ""),
                size=int(raw.get("size") or 0),
                price=str(raw.get("price") or "free"),
                entitlement=str(raw.get("entitlement") or ""),
                icon=str(raw.get("icon") or ""),
                sig=str(raw.get("sig") or ""),
                publisher_id=str(raw.get("publisher_id") or ""),
                installers=_parse_installers(raw.get("installers")),
                delivery=_parse_delivery(raw.get("delivery")),
            )
        )
    publishers = None
    if schema >= 2:
        publishers = parse_publisher_roster(data.get("publishers") or {})
    return RegistryIndex(
        name=str(data.get("name") or ""),
        publisher=str(data.get("publisher") or ""),
        publisher_key=str(data.get("publisher_key") or ""),
        bundles=tuple(entries),
        schema=schema,
        publishers=publishers,
        engines=_parse_engines(data.get("engine")),
        web_host=str((data.get("web") or {}).get("host") or "")
        if isinstance(data.get("web"), dict)
        else "",
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
