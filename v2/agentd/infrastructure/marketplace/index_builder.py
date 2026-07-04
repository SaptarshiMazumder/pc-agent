"""Registry index builder — publisher-side tooling (`agentd bundle index`).

Scan a directory of .agentpkg files -> index.json (schema 1). The SAME folder is a
complete local registry (point registry_url at it) and, uploaded anywhere static,
the cloud registry — that's the whole v0 backend. With a signing key, every entry
gets an ed25519 signature over its sha256 (M7), and the public key is embedded."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agentd.infrastructure import signing
from agentd.infrastructure.marketplace import bundle_io

log = logging.getLogger("agentd")


def build_index(directory: Path, name: str = "", publisher: str = "",
                private_key_b64: str = "", public_key_b64: str = "") -> Path:
    """-> writes <directory>/index.json and returns its path."""
    entries = []
    for package_path in sorted(directory.glob("*.agentpkg")):
        manifest = bundle_io.read_manifest(package_path)
        digest = bundle_io.sha256_file(package_path)
        entry = {
            "id": manifest.id, "name": manifest.name, "version": manifest.version,
            "description": manifest.description, "agentd_compat": manifest.agentd_compat,
            "entitlement": manifest.entitlement, "price": "free" if not manifest.entitlement else "paid",
            "url": package_path.name,          # relative: works from disk AND a CDN
            "sha256": digest, "size": package_path.stat().st_size,
        }
        if private_key_b64:
            entry["sig"] = signing.sign(private_key_b64, digest.encode("ascii"))
        entries.append(entry)
        log.info("indexed %s %s (%s)", manifest.id, manifest.version, package_path.name)
    index = {"schema": 1, "name": name or directory.name, "publisher": publisher,
             "publisher_key": public_key_b64, "bundles": entries}
    index_path = directory / "index.json"
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index_path
