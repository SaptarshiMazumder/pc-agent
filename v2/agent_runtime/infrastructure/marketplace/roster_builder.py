"""Publisher-side roster tooling — build and sign the list of creators a registry trusts.

The roster is the ONLY thing the platform root key ever signs. Creators sign their own bundles
with their own keys, and those private keys never reach us; the platform's job is to say who is
who. That split is what makes a publish API safe to run as a service: the service verifies a
submission against the submitter's ALREADY-listed key, and never needs the root private key to do
it. The root key stays offline (a CI secret), used only when the roster itself changes.

`issued` is supplied by the caller rather than read from the clock here, so building a roster is a
pure function of its inputs — the same inputs produce the same bytes, which is what makes a signed
artifact reviewable in a diff.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.domain.bundle import (
    BundleError,
    PublisherEntry,
    parse_publisher_roster,
    roster_signing_payload,
)
from agent_runtime.infrastructure import signing


def build_roster(
    entries: list[dict],
    revoked: list[str],
    issued: str,
    root_private_b64: str,
    root_public_b64: str = "",
) -> dict:
    """-> the signed `publishers` block for index.json (schema 2).

    `root_key` is recorded in the block for the BUILDER's benefit (publish copies it into the
    index's `publisher_key`) and is deliberately NOT part of the signed payload. A client never
    reads it: it verifies against the key baked into its own installer, which is the only version
    of that key an attacker cannot rewrite.
    """
    parsed = tuple(
        PublisherEntry(
            id=str(e.get("id") or "").strip(),
            key=str(e.get("key") or "").strip(),
            name=str(e.get("name") or "").strip(),
            added=str(e.get("added") or "").strip(),
        )
        for e in entries
        if str(e.get("id") or "").strip() and str(e.get("key") or "").strip()
    )
    clean_revoked = tuple(sorted({str(r).strip() for r in revoked if str(r).strip()}))
    block: dict = {
        "roster": [
            {"id": e.id, "name": e.name or e.id, "key": e.key, "added": e.added}
            for e in sorted(parsed, key=lambda e: e.id)
        ],
        "revoked": list(clean_revoked),
        "issued": issued,
    }
    if root_public_b64:
        block["root_key"] = root_public_b64
    if root_private_b64:
        block["sig"] = signing.sign(
            root_private_b64, roster_signing_payload(parsed, clean_revoked, issued)
        )
    return block


def read_roster_file(path: Path) -> dict:
    """A roster on disk -> its `publishers` block. Raises BundleError with a usable message."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BundleError(f"cannot read roster {path}: {e}") from e
    if not isinstance(data, dict):
        raise BundleError(f"roster {path} is not a JSON object")
    # Accept either a bare publishers block or a whole index.json, so the same command works
    # against the file you keep and against the registry you already published.
    block = data.get("publishers") if "publishers" in data else data
    parse_publisher_roster(block or {})  # validate shape now, not at install time on a user's machine
    return block or {}


def write_roster_file(path: Path, block: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(block, indent=2) + "\n", encoding="utf-8")


def with_publisher(
    block: dict,
    *,
    publisher_id: str,
    name: str,
    key: str,
    issued: str,
    root_private_b64: str,
    root_public_b64: str = "",
) -> dict:
    """Add or REPLACE one creator, then re-sign. Replacing is how a creator rotates their key."""
    entries = [e for e in (block.get("roster") or []) if str(e.get("id") or "") != publisher_id]
    entries.append({"id": publisher_id, "name": name or publisher_id, "key": key, "added": issued})
    # Re-listing a revoked id un-revokes it, which is the only sane reading of "add this creator"
    # — otherwise a re-added creator would be silently inert and nobody would know why.
    revoked = [r for r in (block.get("revoked") or []) if str(r) != publisher_id]
    return build_roster(
        entries, revoked, issued, root_private_b64, root_public_b64 or str(block.get("root_key") or "")
    )


def without_publisher(
    block: dict, *, publisher_id: str, issued: str, root_private_b64: str, root_public_b64: str = ""
) -> dict:
    """Revoke a creator. Their row STAYS on the roster and joins `revoked`.

    Deleting the row would work just as well for blocking installs, and would destroy the record
    of who they were and which key was theirs — the two facts an incident review needs most.
    """
    revoked = sorted({*(str(r) for r in (block.get("revoked") or [])), publisher_id})
    return build_roster(
        list(block.get("roster") or []),
        revoked,
        issued,
        root_private_b64,
        root_public_b64 or str(block.get("root_key") or ""),
    )
