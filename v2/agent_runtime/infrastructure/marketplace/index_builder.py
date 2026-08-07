"""Registry index builder — publisher-side tooling (`agentd bundle index`).

Scan a directory of .agentpkg files -> index.json (schema 1). The SAME folder is a
complete local registry (point registry_url at it) and, uploaded anywhere static,
the cloud registry — that's the whole v0 backend. With a signing key, every entry
gets an ed25519 signature over its sha256 (M7), and the public key is embedded."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.marketplace import bundle_io

log = logging.getLogger("agentd")


def build_index(
    directory: Path,
    name: str = "",
    publisher: str = "",
    private_key_b64: str = "",
    public_key_b64: str = "",
    carry_entries: tuple = (),
) -> Path:
    """-> writes <directory>/index.json and returns its path.

    `carry_entries` are entries from an EXISTING index to keep, for bundles this directory does
    not contain. It exists because an index is the registry's entire contents: rebuilding one from
    a directory holding a single new .agentpkg publishes a registry with a single bundle in it, and
    every other agent silently vanishes from every store — an accidental mass-unpublish with no
    error and no warning. Carrying the prior entries turns publishing into an ADD.

    A carried entry is safe to keep without its artifact present: `url` still points at the object
    already in the registry, and its recorded sha256 is what the signature covers, so it can be
    re-signed here without re-downloading a single byte. Anything the directory DOES contain wins
    (that is how you replace a version), and entries too damaged to sign are dropped loudly rather
    than carried into a fresh index as garbage.
    """
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
        if private_key_b64:
            entry["sig"] = signing.sign(private_key_b64, digest.encode("ascii"))
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
        # Re-sign rather than copy the old signature, so rotating the key re-signs the whole
        # registry in one publish instead of leaving a mix only the old key can verify.
        if private_key_b64:
            carried["sig"] = signing.sign(private_key_b64, digest.encode("ascii"))
        else:
            carried.pop("sig", None)  # an unsigned index must not carry signatures it cannot vouch for
        entries.append(carried)
        log.info("carried %s %s (already published)", bundle_id, prior.get("version") or "?")

    entries.sort(key=lambda e: (str(e.get("id") or ""), str(e.get("version") or "")))
    index = {
        "schema": 1,
        "name": name or directory.name,
        "publisher": publisher,
        "publisher_key": public_key_b64,
        "bundles": entries,
    }
    index_path = directory / "index.json"
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index_path
