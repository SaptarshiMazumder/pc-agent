"""Registry index builder — publisher-side tooling (`agentd bundle index` / `publish`).

Scan a directory of .agentpkg files -> index.json. The SAME folder is a complete local registry
(point registry_url at it) and, uploaded anywhere static, the cloud registry — that's the whole v0
backend. With a signing key, every entry gets an ed25519 signature over its sha256.

TWO SCHEMAS, and which one is emitted depends on whether a roster is supplied:

  * schema 1 — one publisher. `publisher_key` is that publisher's public key and it signed
    everything. What a single-vendor store is, and what already-installed clients understand.
  * schema 2 — many creators. `publisher_key` is the PLATFORM ROOT key, `publishers` is the roster
    it signed, and each bundle carries the `publisher_id` of the creator whose key signed it.

The migration between them is deliberately boring: make the existing publisher key the root key.
Entries carried over from a schema-1 index have no `publisher_id`, so a client falls back to the
pinned key for them — which is that same key. Nothing already installed breaks, and nothing needs
re-signing on the day the second creator appears.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.marketplace import bundle_io

log = logging.getLogger("agentd")

# ── Installers ──────────────────────────────────────────────────────────────────────────
#
# A .agentpkg only reaches someone who ALREADY runs agentd — a daemon installs it. The
# marketplace also has to serve a person on a bare machine, and what they need is an installer.
# Both live in the same directory and go up to the same static host; the index just describes
# them separately.
#
# The link between an installer file and its bundle is a NAMING CONVENTION, because there is
# nothing inside a .exe to read an agent id out of:
#
#     <bundle-id>-<version>-setup<ext>        e.g. game-master-0.2.0-setup.exe
#
# The publisher tooling renames electron-builder's output ("Game Master Setup 0.2.0.exe") into
# this shape. Anything in the directory that does not match is ignored rather than guessed at —
# a mis-attached installer would offer one agent's download on another agent's card.
PLATFORM_BY_EXT = {
    ".exe": "win",
    ".msi": "win",
    ".dmg": "mac",
    ".pkg": "mac",
    ".appimage": "linux",
    ".deb": "linux",
    ".rpm": "linux",
}

INSTALLER_MARKER = "-setup"


def installer_name(bundle_id: str, version: str, suffix: str) -> str:
    """The one place the convention is spelled, so the writer and the reader cannot drift."""
    return f"{bundle_id}-{version}{INSTALLER_MARKER}{suffix.lower()}"


def _installers(directory: Path, bundle_id: str, version: str, private_key_b64: str) -> list[dict]:
    """Installer rows for one bundle, discovered in `directory` by the convention above.

    Each row's digest is signed SEPARATELY from the bundle's. The entry `sig` covers only the
    .agentpkg digest, so unsigned installer rows would let anyone able to write to the registry
    point the download at a different executable without breaking a single signature.
    """
    rows: list[dict] = []
    for suffix, platform in sorted(PLATFORM_BY_EXT.items()):
        path = directory / installer_name(bundle_id, version, suffix)
        if not path.is_file():
            continue
        digest = bundle_io.sha256_file(path)
        row = {
            "platform": platform,
            "url": path.name,  # relative, exactly like the bundle url
            "size": path.stat().st_size,
            "sha256": digest,
        }
        if private_key_b64:
            row["sig"] = signing.sign(private_key_b64, digest.encode("ascii"))
        rows.append(row)
        log.info(
            "  installer %s %s -> %s (%s, %.1f MB)",
            bundle_id,
            version,
            path.name,
            platform,
            path.stat().st_size / 1048576,
        )
    return rows


def build_index(
    directory: Path,
    name: str = "",
    publisher: str = "",
    private_key_b64: str = "",
    public_key_b64: str = "",
    carry_entries: tuple = (),
    publisher_id: str = "",
    roster: dict | None = None,
    root_key_b64: str = "",
) -> Path:
    """-> writes <directory>/index.json and returns its path.

    `private_key_b64` signs the bundles found in `directory` — the PUBLISHER's key in schema 1, the
    CREATOR's own key in schema 2. `publisher_id` stamps those entries with who that creator is.
    `roster` (the signed block from roster_builder) switches the output to schema 2, and
    `root_key_b64` is then the pinned platform key that signed the roster.

    `carry_entries` are entries from an EXISTING index to keep, for bundles this directory does
    not contain. It exists because an index is the registry's entire contents: rebuilding one from
    a directory holding a single new .agentpkg publishes a registry with a single bundle in it, and
    every other agent silently vanishes from every store — an accidental mass-unpublish with no
    error and no warning. Carrying the prior entries turns publishing into an ADD.

    A carried entry is safe to keep without its artifact present: `url` still points at the object
    already in the registry, and its recorded sha256 is what the signature covers. Anything the
    directory DOES contain wins (that is how you replace a version), and entries too damaged to
    keep are dropped loudly rather than carried into a fresh index as garbage.
    """
    multi = roster is not None
    entries = []
    for package_path in sorted(directory.glob("*.agentpkg")):
        manifest = bundle_io.read_manifest(package_path)
        digest = bundle_io.sha256_file(package_path)
        entry = {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "agentd_compat": manifest.agentd_compat,
            "entitlement": manifest.entitlement,
            "price": "free" if not manifest.entitlement else "paid",
            "icon": manifest.icon,
            "url": package_path.name,  # relative: works from disk AND a CDN
            "sha256": digest,
            "size": package_path.stat().st_size,
        }
        if publisher_id:
            entry["publisher_id"] = publisher_id
        if private_key_b64:
            entry["sig"] = signing.sign(private_key_b64, digest.encode("ascii"))
        installers = _installers(directory, manifest.id, manifest.version, private_key_b64)
        if installers:
            entry["installers"] = installers
        entries.append(entry)
        log.info("indexed %s %s (%s)", manifest.id, manifest.version, package_path.name)

    fresh_ids = {str(e["id"]) for e in entries}
    for prior in carry_entries:
        if not isinstance(prior, dict):
            continue
        bundle_id = str(prior.get("id") or "")
        if not bundle_id or bundle_id in fresh_ids:
            continue  # this publish supersedes it
        digest = str(prior.get("sha256") or "")
        if not prior.get("url") or not digest:
            log.warning("dropping unusable prior entry %r (no url/sha256)", bundle_id)
            continue
        carried = dict(prior)
        if multi:
            # Another creator's bundle, signed with a key we do not have and must never have. It is
            # carried EXACTLY as it stands. (Schema 1 re-signed everything, which only worked
            # because one key covered the whole registry; doing it here would silently unpublish
            # every other creator's bundles the moment a second creator published.)
            pass
        elif private_key_b64:
            # One publisher, one key: re-signing keeps a key rotation from leaving a mixed registry
            # that only the old key can verify.
            carried["sig"] = signing.sign(private_key_b64, digest.encode("ascii"))
        else:
            carried.pop("sig", None)  # an unsigned index must not carry signatures it cannot vouch for
        entries.append(carried)
        log.info("carried %s %s (already published)", bundle_id, prior.get("version") or "?")

    entries.sort(key=lambda e: (str(e.get("id") or ""), str(e.get("version") or "")))
    index = {
        "schema": 2 if multi else 1,
        "name": name or directory.name,
        "publisher": publisher,
        "publisher_key": (root_key_b64 if multi else public_key_b64),
        "bundles": entries,
    }
    if multi:
        index["publishers"] = roster
    index_path = directory / "index.json"
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index_path
